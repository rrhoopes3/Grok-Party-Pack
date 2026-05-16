"""
Single-agent executor with client-side tool-calling loop.

Supports multiple providers:
  - xAI (grok-*): native xai_sdk
  - Anthropic (claude-*): Anthropic Messages API
  - OpenAI (gpt-*, o3-*): OpenAI Chat Completions API
  - LM Studio (lmstudio:*): OpenAI-compatible local server
  - Ollama (ollama:*): Ollama local server (OpenAI-compatible)
"""
from __future__ import annotations
import json
import logging
import time
import threading
from datetime import datetime, timezone
from typing import Generator
from xai_sdk import Client
from xai_sdk.chat import user, tool_result
from xai_sdk.tools import get_tool_call_type

from forge.mcp_client import maybe_auto_sync as _maybe_auto_sync
from forge.config import (
    EXECUTOR_MODEL, EXECUTOR_MAX_ITERATIONS,
    LMSTUDIO_BASE_URL, OLLAMA_BASE_URL,
    supports_tools,
)
from forge.tools.registry import ToolRegistry
from forge.tools.escalation import EscalationError
from forge.guardrails import GuardrailEngine
from forge.providers import detect_provider, run_anthropic, run_openai, calculate_cost

log = logging.getLogger("forge.executor")

MAX_RETRIES = 3
RETRY_DELAYS = [2, 4, 8]  # seconds


class UnrecoverableModelError(RuntimeError):
    """Raised when the model rejects the request in a way retry won't fix
    (auth/entitlement/quota errors). The orchestrator should jump straight
    to the fallback chain instead of burning retries."""


# Substrings in API error messages that indicate the model will NEVER serve
# this request — no point in retrying. Hit on these → fail fast.
_UNRECOVERABLE_ERROR_MARKERS = (
    "beta access",                     # xAI: multi-agent tier mismatch
    "INVALID_ARGUMENT",                # gRPC: malformed/unentitled request
    "insufficient_quota",              # OpenAI: billing exhausted (JSON code)
    "exceeded your current quota",     # OpenAI: 429 human-readable message
    "model not found",                 # generic: model decommissioned
    "does not exist",                  # OpenAI: bad model ID
    "permission_denied",               # generic: not entitled
    "permissionsdenied",               # gRPC variant
    "401",                             # auth failure (will repeat)
    "Invalid API Key",
    "authentication failed",
    "billing details",                 # OpenAI quota body fragment
)


def _is_unrecoverable(exc: BaseException) -> bool:
    """Return True if this error means retries are pointless."""
    msg = str(exc)
    return any(marker.lower() in msg.lower() for marker in _UNRECOVERABLE_ERROR_MARKERS)


def _current_timestamp() -> str:
    """Return a compact timestamp string for injection into prompts."""
    now = datetime.now()
    utc = datetime.now(timezone.utc)
    return (
        f"Time: {now.strftime('%Y-%m-%d %H:%M')} local / "
        f"{utc.strftime('%Y-%m-%dT%H:%M')}Z UTC"
    )


EXECUTOR_SYSTEM_BASE = """Forge Executor — autonomous agent with tools.

{timestamp}

Rules: absolute paths only; leverage context; be concise & focused; execute python; dependencies avail."""

EXECUTOR_TRADING_ADDENDUM = """
## Trading Tools (Live Brokerage)
1. Orders are real unless Paper Mode active. Research ≠ Buy Advice.
2. Always quote via `get_market_quote` first; ask for missing qty.
3. Robinhood Crypto applies only to crypto.
4. Report results post-trade.
5. Verify paper/live mode status."""

# Keep the old name for backward compat (tests that import EXECUTOR_SYSTEM_TEMPLATE)
EXECUTOR_SYSTEM_TEMPLATE = EXECUTOR_SYSTEM_BASE + EXECUTOR_TRADING_ADDENDUM

_TRADING_TOOLS = {
    "fetch_pcr", "analyze_sentiment", "get_options_chain", "set_alert",
    "get_portfolio", "execute_trade", "get_market_quote",
    "start_trading_agent", "stop_trading_agent", "get_trading_agent_status",
}


def _build_system_prompt(tool_filter: set[str] | None = None) -> str:
    """Build the executor system prompt with current timestamp.

    Only includes the trading addendum when trading tools are in the filter
    (or when no filter is set, meaning all tools are available).
    """
    prompt = EXECUTOR_SYSTEM_BASE.format(timestamp=_current_timestamp())
    if tool_filter is None or tool_filter & _TRADING_TOOLS:
        prompt += EXECUTOR_TRADING_ADDENDUM
    return prompt


# Backward compat — static reference for tests that import this
EXECUTOR_SYSTEM = EXECUTOR_SYSTEM_TEMPLATE.format(timestamp="(timestamp injected at runtime)")


def execute_step(
    client: Client | None,
    registry: ToolRegistry,
    step_title: str,
    step_description: str,
    context: str = "",
    sandbox_path: str = "",
    cancel_event: threading.Event | None = None,
    model: str = "",
    max_iterations: int = 0,
    tool_filter: set[str] | None = None,
    task_goal: str = "",
    guardrail_engine: GuardrailEngine | None = None,
    system_prompt_override: str = "",
) -> Generator[dict, None, str]:
    """
    Execute a single plan step using the reasoning model + client-side tools.

    Yields SSE-style dicts: {"type": "...", ...}
    Returns the final text output.

    Routes to the correct provider based on model name prefix.

    tool_filter: if set, only these tools are made available (lazy discovery).
    task_goal: original task description, used for instruction reminders.
    """
    use_model = model if model else EXECUTOR_MODEL
    iteration_limit = max_iterations if max_iterations > 0 else EXECUTOR_MAX_ITERATIONS
    provider = detect_provider(use_model)

    # ── Capability gate: refuse to dispatch tool steps to a model that
    # cannot perform client-side tool calls. Without this, every tool call
    # burns the full retry budget (3 attempts × 2-8s backoff) before the
    # orchestrator falls back. Surface the issue immediately instead.
    needs_tools = bool(tool_filter is None or len(tool_filter) > 0)
    if needs_tools and not supports_tools(use_model):
        msg = (f"Model '{use_model}' does not support client-side tool calls — "
               f"refusing to dispatch a tool-using step. Reassign to a "
               f"tool-capable executor.")
        log.error(msg)
        yield {"type": "error", "content": msg}
        raise UnrecoverableModelError(msg)

    log.info("Using executor model: %s (provider: %s, max %d iterations, tools: %s)",
             use_model, provider, iteration_limit,
             f"{len(tool_filter)} filtered" if tool_filter else "all")

    # Build the system prompt with live timestamp (conditionally includes trading rules)
    system_prompt = system_prompt_override if system_prompt_override else _build_system_prompt(tool_filter)

    # Build the full prompt (shared across all providers)
    prompt = f"{system_prompt}\n\n"

    # Inject environment context so the model doesn't need tool calls for basics
    import os as _os
    cwd = sandbox_path or _os.getcwd()
    prompt += (
        f"Environment:\n"
        f"  Working directory: {cwd}\n"
        f"  Platform: {_os.name}\n\n"
    )

    if sandbox_path:
        prompt += f"SANDBOX MODE ACTIVE: All file operations are restricted to {sandbox_path}. Do not attempt to access paths outside this directory.\n\n"
    if context:
        prompt += f"Context from previous steps:\n{context}\n\n"
    prompt += f"Execute this step:\nTitle: {step_title}\nDescription: {step_description}\n\nUse your tools to complete this. Begin."

    # ── Route to non-xAI providers ───────────────────────────────────
    if provider == "anthropic":
        return (yield from run_anthropic(
            model=use_model, system_prompt=system_prompt, user_prompt=prompt,
            registry=registry, sandbox_path=sandbox_path,
            cancel_event=cancel_event, max_iterations=iteration_limit,
            tool_filter=tool_filter, task_goal=task_goal,
            guardrail_engine=guardrail_engine,
        ))
    elif provider == "openai":
        return (yield from run_openai(
            model=use_model, system_prompt=system_prompt, user_prompt=prompt,
            registry=registry, sandbox_path=sandbox_path,
            cancel_event=cancel_event, max_iterations=iteration_limit,
            tool_filter=tool_filter, task_goal=task_goal,
            guardrail_engine=guardrail_engine,
        ))