"""
Autonomous crypto trading agent.

Runs on a configurable interval, feeds market context to an AI model,
and lets it decide whether to trade based on the selected strategy.
Streams decisions back to the UI via SSE.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from queue import Queue

log = logging.getLogger("forge.trading.crypto_agent")


@dataclass
class AgentConfig:
    model: str = "grok-4.20-0309-reasoning"
    strategy: str = "manual"
    ticker: str = "BTC"
    max_position_usd: float = 50.0
    interval_minutes: int = 15


@dataclass
class AgentState:
    running: bool = False
    config: AgentConfig = field(default_factory=AgentConfig)
    last_run: float = 0
    last_decision: str = ""
    cycle_count: int = 0
    error: str = ""


# ── Singleton ────────────────────────────────────────────────────────────────

_agent_lock = threading.Lock()
_agent_thread: threading.Thread | None = None
_agent_state = AgentState()
_agent_cancel = threading.Event()
_agent_log: Queue = Queue(maxsize=500)  # rolling log of agent events


def get_state() -> dict:
    """Return serializable agent state."""
    with _agent_lock:
        return {
            "running": _agent_state.running,
            "config": {
                "model": _agent_state.config.model,
                "strategy": _agent_state.config.strategy,
                "ticker": _agent_state.config.ticker,
                "max_position_usd": _agent_state.config.max_position_usd,
                "interval_minutes": _agent_state.config.interval_minutes,
            },
            "last_run": _agent_state.last_run,
            "last_decision": _agent_state.last_decision,
            "cycle_count": _agent_state.cycle_count,
            "error": _agent_state.error,
        }


def get_logs(limit: int = 50) -> list[dict]:
    """Drain up to `limit` log entries."""
    entries = []
    while not _agent_log.empty() and len(entries) < limit:
        try:
            entries.append(_agent_log.get_nowait())
        except Exception:
            break
    return entries


def _emit(event_type: str, message: str, data: dict | None = None):
    """Push a log event to the agent log queue."""
    entry = {
        "type": event_type,
        "time": datetime.now(timezone.utc).isoformat(),
        "message": message,
    }
    if data:
        entry["data"] = data
    try:
        if _agent_log.full():
            _agent_log.get_nowait()  # drop oldest
        _agent_log.put_nowait(entry)
    except Exception:
        pass
    log.info("[agent:%s] %s", event_type, message)


# ── Strategy prompts ─────────────────────────────────────────────────────────

STRATEGY_PROMPTS = {
    "manual": (
        "You are a crypto market analyst. Analyze the current data and recommend "
        "whether to BUY, SELL, or HOLD {ticker}. Explain your reasoning in 2-3 sentences. "
        "Do NOT execute any trades — just provide your recommendation."
    ),
    "dca": (
        "You are a DCA (Dollar Cost Average) trading agent for {ticker}. "
        "Your job: if we don't already hold {ticker} or our position is below "
        "${max_pos} in value, place a small buy order (10-20% of max position). "
        "If our position already exceeds ${max_pos}, HOLD. "
        "Use get_market_quote to check price, get_portfolio to check position, "
        "then decide. Execute the trade if appropriate."
    ),
    "momentum": (
        "You are a momentum trading agent for {ticker}. "
        "Check the current price with get_market_quote. "
        "If the price change is positive (uptrend), consider buying up to ${max_pos} total. "
        "If negative (downtrend) and we hold a position, consider selling. "
        "If flat, HOLD. Use get_portfolio to see current position. "
        "Execute trades if the signal is clear. Be conservative — only trade on strong signals."
    ),
    "grid": (
        "You are a grid trading agent for {ticker}. "
        "Check the current price. Maintain a position worth roughly half of ${max_pos}. "
        "If price has dropped significantly from our avg entry, buy more (up to max). "
        "If price has risen significantly above avg entry, sell some to take profit. "
        "If near average, HOLD. Use get_portfolio and get_market_quote to decide."
    ),
}


AGENT_SYSTEM_PROMPT = """You are an autonomous crypto trading agent running inside The Forge.
You execute trades programmatically — there is NO human in the loop during your cycle.

{timestamp}

You have trading tools available. Use them to gather data and execute trades.

Rules:
- You ARE authorized to execute trades autonomously. Do not ask for confirmation.
- Respect the max position size constraint strictly.
- Use get_market_quote with provider='robinhood-crypto' for price data.
- Use get_portfolio with provider='robinhood-crypto' to check current holdings before trading.
- Use execute_trade with provider='robinhood-crypto' to place orders. Always specify the ticker and quantity.
- Be conservative with position sizes. Never go all-in.
- End your response with a clear DECISION line: BUY X qty / SELL X qty / HOLD — reason.
"""


def _build_agent_system_prompt() -> str:
    """Build the agent system prompt with timestamp."""
    now = datetime.now(timezone.utc)
    timestamp = f"Current date/time: {now.strftime('%Y-%m-%d %H:%M UTC')}"
    return AGENT_SYSTEM_PROMPT.format(timestamp=timestamp)


def _build_agent_prompt(config: AgentConfig) -> str:
    """Build the strategy-specific prompt with market context."""
    template = STRATEGY_PROMPTS.get(config.strategy, STRATEGY_PROMPTS["manual"])
    strategy_prompt = template.format(
        ticker=config.ticker,
        max_pos=config.max_position_usd,
    )

    return (
        f"## Strategy\n{strategy_prompt}\n\n"
        f"## Constraints\n"
        f"- Max position size: ${config.max_position_usd}\n"
        f"- Ticker: {config.ticker}\n"
        f"- Provider: robinhood-crypto\n"
        f"- This is LIVE trading with real money.\n\n"
        f"Execute your strategy now. End with DECISION: BUY / SELL / HOLD and why."
    )


# ── Agent loop ───────────────────────────────────────────────────────────────

def _run_cycle(config: AgentConfig) -> str:
    """Run a single analysis/trade cycle. Returns the agent's decision text."""
    from forge.executor import execute_step
    from forge.tools.registry import ToolRegistry
    from forge.tools import trading as trading_tools

    # Build a registry with only trading tools
    registry = ToolRegistry()
    trading_tools.register(registry)

    prompt = _build_agent_prompt(config)

    # Create the appropriate client for the model provider
    from forge.providers import detect_provider
    provider = detect_provider(config.model)
    client = None
    if provider == "xai":
        from xai_sdk import Client
        from forge.config import XAI_API_KEY
        client = Client(api_key=XAI_API_KEY)

    # Use autonomous system prompt for non-manual strategies
    sys_prompt = _build_agent_system_prompt() if config.strategy != "manual" else ""

    # Run the executor — collect all output
    gen = execute_step(
        client=client,
        registry=registry,
        step_title=f"Crypto Agent: {config.strategy} on {config.ticker}",
        step_description=prompt,
        model=config.model,
        max_iterations=8,
        tool_filter={"get_market_quote", "get_portfolio", "execute_trade",
                     "fetch_pcr", "analyze_sentiment"},
        system_prompt_override=sys_prompt,
    )

    full_text = ""
    tool_calls = []
    try:
        while True:
            msg = next(gen)
            msg_type = msg.get("type", "")

            if msg_type == "content":
                chunk = msg.get("content", "")
                full_text += chunk

            elif msg_type == "tool-call":
                name = msg.get("name", "")
                args = msg.get("arguments", msg.get("args", {}))
                tool_calls.append({"tool": name, "args": args})
                _emit("tool_call", f"Calling {name}({json.dumps(args, default=str)[:200]})")

            elif msg_type == "tool-result":
                result_preview = str(msg.get("result", ""))[:300]
                _emit("tool_result", f"Result: {result_preview}")

            elif msg_type == "error":
                _emit("error", msg.get("content", "Unknown error"))

    except StopIteration as e:
        if e.value:
            full_text = str(e.value)

    # Extract the decision line
    decision = ""
    for line in reversed(full_text.strip().split("\n")):
        line = line.strip()
        if line and any(kw in line.upper() for kw in ["BUY", "SELL", "HOLD", "DECISION"]):
            decision = line
            break
    if not decision:
        decision = full_text.strip().split("\n")[-1] if full_text.strip() else "No output"

    _emit("decision", decision, {"full_text": full_text[:1000], "tool_calls": tool_calls})
    return decision


def _persist_decision(config: AgentConfig, decision: str, cycle: int):
    """Log decision to DB with current price and position for analysis."""
    try:
        from forge.trading.portfolio import get_portfolio_manager
        from forge.trading.portfolio_view import build_portfolio_summary
        from forge.trading.engine import get_engine

        pm = get_portfolio_manager()
        engine = get_engine()

        # Fetch current price
        price = None
        try:
            quote = engine.get_quote(config.ticker, provider="robinhood-crypto")
            price = quote.price
        except Exception:
            pass

        # Get current position
        pos_qty = 0.0
        pos_value = 0.0
        summary = build_portfolio_summary(provider_name="robinhood-crypto")
        for position in summary.get("positions", []):
            if position.get("ticker") == config.ticker:
                pos_qty = float(position.get("quantity", 0) or 0)
                pos_value = float(position.get("market_value", 0) or 0)
                break

        pm.log_decision(
            ticker=config.ticker,
            strategy=config.strategy,
            model=config.model,
            price=price,
            decision=decision,
            position_qty=pos_qty,
            position_value=pos_value,
            cycle=cycle,
        )
        log.info("Decision persisted: %s @ $%s", decision[:80], price)
    except Exception as e:
        log.warning("Failed to persist decision: %s", e)


def _agent_loop():
    """Main agent loop — runs cycles on the configured interval."""
    global _agent_state

    _emit("started", f"Agent started: {_agent_state.config.strategy} on {_agent_state.config.ticker}")

    while not _agent_cancel.is_set():
        config = _agent_state.config  # snapshot config

        try:
            _emit("cycle_start", f"Cycle #{_agent_state.cycle_count + 1} beginning")
            decision = _run_cycle(config)

            with _agent_lock:
                _agent_state.last_run = time.time()
                _agent_state.last_decision = decision
                _agent_state.cycle_count += 1
                _agent_state.error = ""
                cycle_num = _agent_state.cycle_count

            # Persist decision with current price for analysis
            _persist_decision(config, decision, cycle_num)

        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            log.exception("Agent cycle failed")
            _emit("error", f"Cycle failed: {error_msg}")
            with _agent_lock:
                _agent_state.error = error_msg

        # Wait for next interval (check cancel every 5s for responsiveness)
        wait_seconds = config.interval_minutes * 60
        _emit("waiting", f"Next cycle in {config.interval_minutes}m ({wait_seconds}s)")
        elapsed = 0
        while elapsed < wait_seconds and not _agent_cancel.is_set():
            time.sleep(min(5, wait_seconds - elapsed))
            elapsed += 5

    reason = "cancel requested" if _agent_cancel.is_set() else "loop exited unexpectedly"
    _emit("stopped", f"Agent stopped ({reason})")
    log.info("Agent loop exited: cancel=%s, cycles=%d", _agent_cancel.is_set(), _agent_state.cycle_count)
    with _agent_lock:
        _agent_state.running = False


def start(config: AgentConfig) -> dict:
    """Start the agent with given config. Returns state."""
    global _agent_thread, _agent_state, _agent_cancel

    with _agent_lock:
        if _agent_state.running:
            return {"error": "Agent is already running"}

        # Fresh cancel event each run — avoids stale state from previous runs
        _agent_cancel = threading.Event()
        _agent_state = AgentState(running=True, config=config)

    _agent_thread = threading.Thread(target=_agent_loop, daemon=True, name="crypto-agent")
    _agent_thread.start()
    return get_state()


def stop() -> dict:
    """Stop the agent. Returns final state."""
    global _agent_thread

    with _agent_lock:
        if not _agent_state.running:
            return {"error": "Agent is not running"}

    _agent_cancel.set()
    if _agent_thread:
        _agent_thread.join(timeout=15)
        _agent_thread = None

    return get_state()
