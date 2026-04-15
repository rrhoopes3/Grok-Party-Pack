"""
Autonomous Polymarket prediction-market agent.

Targets a specific event URL, evaluates market conditions on a timer,
and recommends / executes trades via AI analysis.

Supports **rotating slugs** — e.g. BTC up/down markets that roll every
15 minutes (btc-updown-15m-{timestamp}).  The agent detects the pattern
from the initial slug, auto-computes the current active slot each cycle,
and seamlessly advances when the window rolls over.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from queue import Queue

import requests as _req

log = logging.getLogger("forge.trading.polymarket_agent")

GAMMA_BASE = "https://gamma-api.polymarket.com"


# ── Rotating-slug helpers ────────────────────────────────────────────────────

# Matches slugs like  btc-updown-15m-1773465300
_ROTATING_RE = re.compile(r"^(.+?-)(\d+)$")

# Known rotation intervals (suffix hint → seconds)
_INTERVAL_HINTS: dict[str, int] = {
    "5m-":  300,
    "15m-": 900,
    "30m-": 1800,
    "1h-":  3600,
}


@dataclass
class RotatingSlug:
    """Parsed rotating-slug pattern.  ``None`` if the slug is static."""
    prefix: str          # e.g. "btc-updown-15m-"
    interval: int        # seconds between slots (e.g. 900)
    seed_ts: int         # original timestamp from user's slug

    def current_slug(self, now_ts: float | None = None) -> str:
        """Return the slug for the currently-active slot."""
        now = int(now_ts or time.time())
        slot_ts = now - (now % self.interval)
        # Sometimes the seed is aligned differently — snap to the grid
        # that includes the seed timestamp
        offset = self.seed_ts % self.interval
        slot_ts = now - ((now - offset) % self.interval)
        return f"{self.prefix}{slot_ts}"

    def upcoming_slugs(self, count: int = 3, now_ts: float | None = None) -> list[str]:
        """Return the next ``count`` slot slugs (current + future)."""
        now = int(now_ts or time.time())
        offset = self.seed_ts % self.interval
        base = now - ((now - offset) % self.interval)
        return [f"{self.prefix}{base + i * self.interval}" for i in range(count)]


def detect_rotating_slug(slug: str) -> RotatingSlug | None:
    """Try to detect a rotating timestamp pattern in *slug*.
    Returns ``None`` for static / non-rotating slugs."""
    m = _ROTATING_RE.match(slug)
    if not m:
        return None
    prefix, ts_str = m.group(1), m.group(2)
    try:
        seed_ts = int(ts_str)
    except ValueError:
        return None
    # The timestamp should be a plausible Unix epoch (after 2020)
    if seed_ts < 1_577_836_800:
        return None

    # Determine interval from prefix hints or fall back to 900
    # Match longest hint first to avoid "5m-" matching "15m-"
    interval = 900
    for hint, secs in sorted(_INTERVAL_HINTS.items(), key=lambda x: -len(x[0])):
        if prefix.endswith(hint):
            interval = secs
            break

    return RotatingSlug(prefix=prefix, interval=interval, seed_ts=seed_ts)


@dataclass
class PolyAgentConfig:
    model: str = "grok-4.20-0309-reasoning"
    strategy: str = "analyst"
    event_slug: str = ""          # e.g. "btc-updown-15m-1773464700"
    event_url: str = ""           # full polymarket URL for reference
    max_position_usd: float = 50.0
    interval_minutes: int = 15
    live_trading: bool = False    # if True, actually place orders (requires CLOB creds)
    dry_run: bool = True          # if True, log orders but don't execute


@dataclass
class PolyAgentState:
    running: bool = False
    config: PolyAgentConfig = field(default_factory=PolyAgentConfig)
    last_run: float = 0
    last_decision: str = ""
    cycle_count: int = 0
    error: str = ""
    active_slug: str = ""         # the slug currently being evaluated
    rotating: RotatingSlug | None = None


# ── Singleton ────────────────────────────────────────────────────────────────

_agent_lock = threading.Lock()
_agent_thread: threading.Thread | None = None
_agent_state = PolyAgentState()
_agent_cancel = threading.Event()
_agent_log: Queue = Queue(maxsize=500)


def get_state() -> dict:
    with _agent_lock:
        rot = _agent_state.rotating
        active = _agent_state.active_slug or _agent_state.config.event_slug
        upcoming = rot.upcoming_slugs(4) if rot else []
        return {
            "running": _agent_state.running,
            "config": {
                "model": _agent_state.config.model,
                "strategy": _agent_state.config.strategy,
                "event_slug": _agent_state.config.event_slug,
                "event_url": _agent_state.config.event_url,
                "max_position_usd": _agent_state.config.max_position_usd,
                "interval_minutes": _agent_state.config.interval_minutes,
            },
            "last_run": _agent_state.last_run,
            "last_decision": _agent_state.last_decision,
            "cycle_count": _agent_state.cycle_count,
            "error": _agent_state.error,
            "active_slug": active,
            "rotating": rot is not None,
            "upcoming_slugs": upcoming,
        }


def get_logs(limit: int = 50) -> list[dict]:
    entries = []
    while not _agent_log.empty() and len(entries) < limit:
        try:
            entries.append(_agent_log.get_nowait())
        except Exception:
            break
    return entries


def _emit(event_type: str, message: str, data: dict | None = None):
    entry = {
        "type": event_type,
        "time": datetime.now(timezone.utc).isoformat(),
        "message": message,
    }
    if data:
        entry["data"] = data
    try:
        if _agent_log.full():
            _agent_log.get_nowait()
        _agent_log.put_nowait(entry)
    except Exception:
        pass
    log.info("[poly-agent:%s] %s", event_type, message)


# ── Fetch event data from Gamma API ──────────────────────────────────────────

def fetch_event(slug: str) -> dict | None:
    """Fetch a Polymarket event by slug from the Gamma API.

    Returns the event dict with inline 'markets' list when fetched by ID.
    """
    try:
        # First: lookup by slug to get the event ID
        resp = _req.get(f"{GAMMA_BASE}/events", params={"slug": slug}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        event = None
        if isinstance(data, list) and data:
            event = data[0]
        elif isinstance(data, dict) and data:
            event = data

        if not event:
            return None

        # Second: fetch by ID — this returns inline markets with live prices
        event_id = event.get("id")
        if event_id:
            try:
                resp2 = _req.get(f"{GAMMA_BASE}/events/{event_id}", timeout=10)
                resp2.raise_for_status()
                full = resp2.json()
                if isinstance(full, dict):
                    return full
            except Exception:
                pass  # fall back to the slug-based result

        return event
    except Exception as e:
        log.warning("Failed to fetch event %s: %s", slug, e)
        return None


def fetch_event_markets(event: dict | None) -> list[dict]:
    """Extract markets from a fully-fetched event dict.

    The /events/{id} endpoint returns markets inline, which is far more
    reliable than the /markets?event_slug= endpoint (which returns garbage).
    """
    if not event:
        return []
    markets = event.get("markets")
    if isinstance(markets, list):
        return markets
    return []


def _fetch_btc_context() -> str:
    """Fetch recent BTC price action for short-term directional analysis."""
    lines = []
    try:
        from forge.trading.engine import get_engine
        engine = get_engine()
        q = engine.get_quote("BTC", provider="robinhood-crypto")
        lines.append(f"## Live BTC Price")
        lines.append(f"  Price: ${q.price:,.2f}")
        if q.change is not None:
            sign = "+" if q.change >= 0 else ""
            lines.append(f"  24h Change: {sign}${q.change:,.2f} ({sign}{q.change_pct:.2f}%)")
        if q.volume:
            lines.append(f"  Volume: {q.volume:,.0f}")
    except Exception as e:
        lines.append(f"## Live BTC Price — unavailable ({e})")

    # Recent 5m candles for momentum read
    try:
        import yfinance as yf
        hist = yf.Ticker("BTC-USD").history(period="1d", interval="5m")
        if not hist.empty:
            recent = hist.tail(12)  # last hour of 5m candles
            lines.append(f"\n## Recent BTC 5-min Candles (last {len(recent)} bars)")
            for idx, row in recent.iterrows():
                t = idx.strftime("%H:%M")
                o, c = float(row["Open"]), float(row["Close"])
                h, l = float(row["High"]), float(row["Low"])
                direction = "▲" if c >= o else "▼"
                lines.append(f"  {t}  O:{o:,.0f} H:{h:,.0f} L:{l:,.0f} C:{c:,.0f} {direction}")

            # Compute short-term momentum signals
            closes = [float(r["Close"]) for _, r in recent.iterrows()]
            if len(closes) >= 6:
                last_6 = closes[-6:]  # last 30 min
                last_3 = closes[-3:]  # last 15 min
                pct_30m = ((last_6[-1] - last_6[0]) / last_6[0]) * 100
                pct_15m = ((last_3[-1] - last_3[0]) / last_3[0]) * 100
                up_bars = sum(1 for i in range(1, len(last_6)) if last_6[i] > last_6[i-1])
                lines.append(f"\n## Momentum Signals")
                lines.append(f"  30m change: {pct_30m:+.3f}%")
                lines.append(f"  15m change: {pct_15m:+.3f}%")
                lines.append(f"  Up bars (last 6): {up_bars}/5")
                lines.append(f"  Trend: {'BULLISH' if pct_15m > 0.05 else 'BEARISH' if pct_15m < -0.05 else 'FLAT'}")
    except Exception as e:
        lines.append(f"\n## Recent candles unavailable ({e})")

    return "\n".join(lines)


def _build_market_context(config: PolyAgentConfig) -> str:
    """Fetch current market data and build context string for the AI."""
    slug = config.event_slug
    event = fetch_event(slug)
    markets = fetch_event_markets(event)

    lines = []

    # ── PRICES FIRST — the most important data point ──
    # Put this at the very top so the model sees it immediately
    for m in markets:
        outcomes = []
        try:
            names = json.loads(m["outcomes"]) if isinstance(m.get("outcomes"), str) else (m.get("outcomes") or [])
            prices = json.loads(m["outcomePrices"]) if isinstance(m.get("outcomePrices"), str) else (m.get("outcomePrices") or [])
            outcomes = [(n, float(p)) for n, p in zip(names, prices)]
        except Exception:
            pass
        if outcomes:
            lines.append("## CURRENT POLYMARKET PRICES (what the crowd is betting)")
            for name, price in outcomes:
                cents = price * 100
                lines.append(f"  {name.upper()}: {cents:.1f}¢  →  crowd thinks {cents:.1f}% chance")
            lines.append(f"  Volume: ${float(m.get('volume24hr', 0)):,.0f}  |  Liquidity: ${float(m.get('liquidity', 0)):,.0f}")
            lines.append("")

    # ── Event metadata ──
    lines.append(f"## Event: {slug}")
    if event:
        lines.append(f"Title: {event.get('title', 'Unknown')}")
        desc = event.get('description', '')
        # Pull out just the resolution logic, skip the boilerplate
        if 'resolve' in desc.lower():
            resolve_line = [s.strip() for s in desc.split('.') if 'resolve' in s.lower()]
            if resolve_line:
                lines.append(f"Resolution: {resolve_line[0]}.")
        lines.append(f"End: {event.get('endDate', 'N/A')}")

    if not markets:
        lines.append("\nWARNING: No markets found — prices unavailable.")

    # ── BTC price + momentum data ──
    if "btc" in slug.lower():
        lines.append("\n" + _fetch_btc_context())

    return "\n".join(lines)


# ── Strategy prompts ─────────────────────────────────────────────────────────

STRATEGY_PROMPTS = {
    "analyst": (
        "You are a short-term BTC prediction market analyst specializing in "
        "15-minute directional bets.\n\n"
        "Your edge comes from reading momentum signals in recent price action:\n"
        "- Check the 5-min candle trend: are recent closes trending up or down?\n"
        "- Check 15m and 30m price change: which direction has momentum?\n"
        "- Count up vs down bars: strong trends show 4-5/5 bars in one direction.\n"
        "- Check if the Polymarket YES/NO prices already reflect the momentum "
        "  (if YES is 75%+ and momentum confirms, the edge is thin).\n\n"
        "KEY INSIGHT: These markets resolve based on whether BTC price at window "
        "close is HIGHER or LOWER than at window open. You're not predicting big "
        "moves — even $1 up means YES wins. Focus on short-term direction, not magnitude.\n\n"
        "Only recommend a trade when you see a clear edge — the market price is "
        "mispriced relative to the actual momentum. If it's a coin flip, say HOLD.\n"
        "Do NOT execute trades — just provide your analysis with confidence %."
    ),
    "contrarian": (
        "You are a contrarian short-term BTC prediction market trader.\n\n"
        "Your thesis: the crowd overreacts to recent momentum. When a market is "
        "heavily skewed (YES >75% or YES <25%), the edge often lies with the "
        "minority outcome because:\n"
        "- BTC is mean-reverting on short timeframes\n"
        "- High skew = everyone piled in after a move that may already be exhausted\n"
        "- A $50 move can flip direction in seconds\n\n"
        "Look for: skewed prices + momentum showing signs of exhaustion "
        "(decelerating candles, lower highs/higher lows, declining bar range).\n"
        "Max position: ${max_pos}."
    ),
    "momentum": (
        "You are a momentum-following short-term BTC prediction market trader.\n\n"
        "Your thesis: short-term BTC trends persist more often than they reverse. "
        "When 4+/5 of the last 5-min bars close in the same direction AND the "
        "15m change confirms, bet on continuation.\n\n"
        "Rules:\n"
        "- Strong momentum (15m change > 0.1%): BUY YES on 'Up' / BUY NO on 'Down'\n"
        "- Moderate momentum (0.03-0.1%): only trade if market price offers edge\n"
        "- Weak/flat (<0.03%): HOLD — not enough signal\n"
        "- Never buy YES above 80¢ or NO above 80¢ — the risk/reward is bad\n"
        "Max position: ${max_pos}."
    ),
    "value": (
        "You are a value-focused short-term BTC prediction market trader.\n\n"
        "Your thesis: estimate the true probability that BTC ends the 15-min "
        "window higher than it started, then compare to market price. Only trade "
        "when you see 10%+ edge.\n\n"
        "Estimating true probability:\n"
        "- Base rate: ~50% (random walk)\n"
        "- Adjust for momentum: strong 15m trend adds ~10-15% to continuation\n"
        "- Adjust for mean reversion: after extended moves, subtract 5-10%\n"
        "- Adjust for volatility: high-vol periods = closer to 50% (noise)\n\n"
        "Example: momentum says 60% chance up, market prices YES at 48¢ "
        "→ 12% edge → BUY YES.\n"
        "Example: momentum says 55% up, market prices YES at 53¢ "
        "→ 2% edge → HOLD (not enough).\n"
        "Max position: ${max_pos}."
    ),
}


AGENT_SYSTEM_PROMPT = """You are an autonomous Polymarket prediction-market agent running inside The Forge.
You trade short-duration BTC directional prediction markets.

{timestamp}

These markets ask: "Will BTC price be HIGHER or LOWER at the end of a 15-minute window
compared to the start?" YES = higher, NO = lower.

You receive:
1. The current Polymarket event with YES/NO prices
2. Live BTC price and 24h change
3. Recent 5-minute candles (last hour) with momentum signals

Your job: determine if there's a tradeable edge between the market price and the
actual probability implied by recent price action.

Rules:
- Think probabilistically. These are short windows — even strong momentum only
  shifts the odds to maybe 60-65%. Don't be overconfident.
- The market price IS the crowd's estimate. You need to disagree AND be right.
- Factor in transaction costs / spread. A 2% edge gets eaten by spread.
- HOLD is always a valid decision. Only trade with conviction.
- Size proportionally to confidence. 55% edge = small. 65% edge = larger.
- End with: DECISION: BUY YES $X / BUY NO $X / HOLD — reason.
- Max position: ${max_pos}.
"""


def _build_agent_system_prompt(config: PolyAgentConfig) -> str:
    now = datetime.now(timezone.utc)
    timestamp = f"Current date/time: {now.strftime('%Y-%m-%d %H:%M UTC')}"
    return AGENT_SYSTEM_PROMPT.format(
        timestamp=timestamp,
        max_pos=config.max_position_usd,
    )


def _build_agent_prompt(config: PolyAgentConfig, market_context: str) -> str:
    template = STRATEGY_PROMPTS.get(config.strategy, STRATEGY_PROMPTS["analyst"])
    strategy_prompt = template.format(max_pos=config.max_position_usd)

    return (
        f"{market_context}\n\n"
        f"## Strategy\n{strategy_prompt}\n\n"
        f"## Constraints\n"
        f"- Max position size: ${config.max_position_usd}\n"
        f"- Event: {config.event_slug}\n"
        f"- Provide clear BUY YES / BUY NO / HOLD decisions with reasoning."
    )


def _cycle_config_for_slug(config: PolyAgentConfig, event_slug: str) -> PolyAgentConfig:
    """Clone agent config for a specific rotating event slug."""
    return PolyAgentConfig(
        model=config.model,
        strategy=config.strategy,
        event_slug=event_slug,
        event_url=config.event_url,
        max_position_usd=config.max_position_usd,
        interval_minutes=config.interval_minutes,
        live_trading=config.live_trading,
        dry_run=config.dry_run,
    )


# ── Agent loop ───────────────────────────────────────────────────────────────

def _run_cycle(config: PolyAgentConfig) -> str:
    """Run a single analysis cycle."""
    from forge.executor import execute_step
    from forge.tools.registry import ToolRegistry

    # Build market context from live data
    _emit("cycle_start", "Fetching market data...")
    market_context = _build_market_context(config)
    _emit("tool_result", f"Market data fetched ({len(market_context)} chars)")

    prompt = _build_agent_prompt(config, market_context)

    # Create AI client
    from forge.providers import detect_provider
    provider = detect_provider(config.model)
    client = None
    if provider == "xai":
        from xai_sdk import Client
        from forge.config import XAI_API_KEY
        client = Client(api_key=XAI_API_KEY)

    sys_prompt = _build_agent_system_prompt(config)

    # Give the agent access to trading tools for live price checks
    # Multi-agent models don't support client-side tools — skip for those
    registry = ToolRegistry()
    tool_filter: set[str] = set()
    is_multi_agent = "multi-agent" in config.model
    if not is_multi_agent:
        from forge.tools import trading as trading_tools
        trading_tools.register(registry)
        tool_filter = {"get_market_quote", "analyze_sentiment"}

    gen = execute_step(
        client=client,
        registry=registry,
        step_title=f"Polymarket Agent: {config.strategy} on {config.event_slug}",
        step_description=prompt,
        model=config.model,
        max_iterations=6 if not is_multi_agent else 2,
        tool_filter=tool_filter,
        system_prompt_override=sys_prompt,
    )

    full_text = ""
    try:
        while True:
            msg = next(gen)
            msg_type = msg.get("type", "")
            if msg_type == "content":
                full_text += msg.get("content", "")
            elif msg_type == "error":
                _emit("error", msg.get("content", "Unknown error"))
    except StopIteration as e:
        if e.value:
            full_text = str(e.value)

    # Extract decision lines
    decision_lines = []
    for line in full_text.strip().split("\n"):
        line_stripped = line.strip()
        if line_stripped and any(kw in line_stripped.upper() for kw in ["BUY YES", "BUY NO", "HOLD", "DECISION"]):
            decision_lines.append(line_stripped)

    decision_text = " | ".join(decision_lines[-3:]) if decision_lines else full_text.strip().split("\n")[-1] if full_text.strip() else "No output"

    _emit("decision", decision_text, {"full_text": full_text[:2000]})

    # ── Execute trade if live trading is enabled ──
    if config.live_trading:
        from forge.trading.polymarket_executor import parse_decision, execute_market_order, is_configured

        trade = parse_decision(full_text)
        if trade.is_trade:
            if not is_configured():
                _emit("error", "Trade signal but CLOB credentials not configured. Set FORGE_POLYMARKET_PRIVATE_KEY.")
            else:
                # Get condition_id from the event data
                event = fetch_event(config.event_slug)
                markets = fetch_event_markets(event)
                condition_id = None
                if markets:
                    condition_id = markets[0].get("conditionId") or markets[0].get("condition_id")

                if not condition_id:
                    _emit("error", f"Cannot execute: no condition_id found for {config.event_slug}")
                else:
                    _emit("trade", f"Executing: {trade.action} {trade.side} ${trade.amount_usd:.0f} {'(DRY RUN)' if config.dry_run else '(LIVE)'}")
                    result = execute_market_order(
                        condition_id=condition_id,
                        decision=trade,
                        max_position_usd=config.max_position_usd,
                        dry_run=config.dry_run,
                    )
                    if result.success:
                        _emit("trade", result.summary())
                    else:
                        _emit("error", f"Trade failed: {result.error}")
        else:
            _emit("trade", "HOLD — no trade this cycle")

    return decision_text


def _agent_loop():
    global _agent_state

    # Detect rotating slug pattern
    rot = detect_rotating_slug(_agent_state.config.event_slug)
    with _agent_lock:
        _agent_state.rotating = rot

    if rot:
        current = rot.current_slug()
        upcoming = rot.upcoming_slugs(3)
        _emit("started",
              f"Agent started (ROTATING): {_agent_state.config.strategy} "
              f"| pattern={rot.prefix}* interval={rot.interval}s "
              f"| current={current}",
              {"upcoming": upcoming})
        with _agent_lock:
            _agent_state.active_slug = current
    else:
        _emit("started", f"Agent started: {_agent_state.config.strategy} on {_agent_state.config.event_slug}")
        with _agent_lock:
            _agent_state.active_slug = _agent_state.config.event_slug

    while not _agent_cancel.is_set():
        config = _agent_state.config

        # Auto-advance rotating slug to current slot
        if rot:
            new_slug = rot.current_slug()
            old_slug = _agent_state.active_slug
            if new_slug != old_slug:
                _emit("rotation", f"Slot rolled: {old_slug} → {new_slug}",
                      {"upcoming": rot.upcoming_slugs(3)})
            with _agent_lock:
                _agent_state.active_slug = new_slug
            # Override the event_slug for this cycle
            cycle_config = _cycle_config_for_slug(config, new_slug)
        else:
            cycle_config = config

        try:
            _emit("cycle_start",
                  f"Cycle #{_agent_state.cycle_count + 1} — targeting {cycle_config.event_slug}")
            decision = _run_cycle(cycle_config)

            with _agent_lock:
                _agent_state.last_run = time.time()
                _agent_state.last_decision = decision
                _agent_state.cycle_count += 1
                _agent_state.error = ""

        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            log.exception("Polymarket agent cycle failed")
            _emit("error", f"Cycle failed: {error_msg}")
            with _agent_lock:
                _agent_state.error = error_msg

        wait_seconds = config.interval_minutes * 60
        _emit("waiting", f"Next cycle in {config.interval_minutes}m ({wait_seconds}s)")
        elapsed = 0
        while elapsed < wait_seconds and not _agent_cancel.is_set():
            time.sleep(min(5, wait_seconds - elapsed))
            elapsed += 5

    reason = "cancel requested" if _agent_cancel.is_set() else "loop exited unexpectedly"
    _emit("stopped", f"Agent stopped ({reason})")
    with _agent_lock:
        _agent_state.running = False


def start(config: PolyAgentConfig) -> dict:
    global _agent_thread, _agent_state, _agent_cancel

    with _agent_lock:
        if _agent_state.running:
            return {"error": "Polymarket agent is already running"}

        _agent_cancel = threading.Event()
        _agent_state = PolyAgentState(running=True, config=config)

    _agent_thread = threading.Thread(target=_agent_loop, daemon=True, name="poly-agent")
    _agent_thread.start()
    return get_state()


def stop() -> dict:
    global _agent_thread

    with _agent_lock:
        if not _agent_state.running:
            return {"error": "Polymarket agent is not running"}

    _agent_cancel.set()
    if _agent_thread:
        _agent_thread.join(timeout=15)
        _agent_thread = None

    return get_state()
