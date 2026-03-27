"""
Multi-provider executor adapters for The Forge.

Each provider implements the same generator interface as the xAI executor:
  yields {"type": "content"|"tool_call"|"tool_result"|"status"|"error"|"cancelled", ...}
  returns full_output: str

Supported providers:
  - xai     → native xai_sdk (handled in executor.py)
  - anthropic → Anthropic Messages API
  - openai  → OpenAI Chat Completions API
  - lmstudio → OpenAI-compatible local server
  - ollama   → Ollama local server (OpenAI-compatible)
"""
from __future__ import annotations
import json
import logging
import threading
from typing import Generator

from forge.config import ANTHROPIC_API_KEY, OPENAI_API_KEY, LMSTUDIO_BASE_URL, OLLAMA_BASE_URL, EXECUTOR_MODELS
from forge.tools.registry import ToolRegistry


def _effective_key(provider: str, fallback: str) -> str:
    """Return the BYOK key for the current request thread, or the global fallback."""
    from forge.config import PUBLIC_MODE
    if PUBLIC_MODE:
        from forge.public_mode import get_request_key
        byok = get_request_key(provider)
        if byok:
            return byok
    return fallback

log = logging.getLogger("forge.providers")


# ── Cost Calculation ──────────────────────────────────────────────────────

def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> dict:
    """Calculate cost in USD from token usage and return a token_usage event dict."""
    info = EXECUTOR_MODELS.get(model, {})
    cost_in = info.get("cost_in", 0)
    cost_out = info.get("cost_out", 0)
    cost_usd = (input_tokens * cost_in + output_tokens * cost_out) / 1_000_000
    return {
        "type": "token_usage",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost_usd, 6),
    }


# ── Provider Detection ─────────────────────────────────────────────────────

def detect_provider(model: str) -> str:
    """Determine which API provider to use based on model name."""
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith(("gpt-", "o1-", "o3-", "o4-", "chatgpt-")):
        return "openai"
    if model.startswith("lmstudio:"):
        return "lmstudio"
    if model.startswith("ollama:"):
        return "ollama"
    return "xai"


# ── Tool Format Converters ─────────────────────────────────────────────────

def _to_anthropic_tools(registry: ToolRegistry, only: set[str] | None = None) -> list[dict]:
    """Convert registry tools to Anthropic format."""
    tools = []
    for t in registry.get_raw_tools(only=only):
        tools.append({
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["parameters"],
        })
    return tools


def _to_openai_tools(registry: ToolRegistry, only: set[str] | None = None) -> list[dict]:
    """Convert registry tools to OpenAI function-calling format."""
    tools = []
    for t in registry.get_raw_tools(only=only):
        tools.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        })
    return tools


# ── Anthropic Adapter ──────────────────────────────────────────────────────

def run_anthropic(
    model: str,
    system_prompt: str,
    user_prompt: str,
    registry: ToolRegistry,
    sandbox_path: str,
    cancel_event: threading.Event | None,
    max_iterations: int,
    tool_filter: set[str] | None = None,
    task_goal: str = "",
    guardrail_engine=None,
) -> Generator[dict, None, str]:
    """Run Anthropic Messages API with tool-calling loop."""
    try:
        import anthropic
    except ImportError:
        yield {"type": "error", "content": "anthropic package not installed. Run: pip install anthropic"}
        return ""

    effective_anthropic_key = _effective_key("anthropic", ANTHROPIC_API_KEY)
    if not effective_anthropic_key:
        yield {"type": "error", "content": "ANTHROPIC_API_KEY not set in .env"}
        return ""

    REMINDER_INTERVAL = 3

    client = anthropic.Anthropic(api_key=effective_anthropic_key)
    tools = _to_anthropic_tools(registry, only=tool_filter)

    messages = [{"role": "user", "content": user_prompt}]
    full_output = ""

    for iteration in range(max_iterations):
        if cancel_event and cancel_event.is_set():
            yield {"type": "cancelled", "content": "Step cancelled"}
            return full_output

        # ── Instruction Reminder ─────────────────────────────────────
        if task_goal and iteration > 0 and iteration % REMINDER_INTERVAL == 0:
            from forge.executor import _current_timestamp
            messages.append({"role": "user", "content":
                f"[SYSTEM REMINDER] {_current_timestamp()}\n"
                f"Original task goal: {task_goal[:500]}\nStay focused."})

        log.info("Anthropic iteration %d (model: %s)", iteration + 1, model)

        try:
            # Stream the response
            collected_text = ""
            tool_uses = []

            with client.messages.stream(
                model=model,
                max_tokens=4096,
                system=system_prompt,
                tools=tools,
                messages=messages,
            ) as stream:
                for event in stream:
                    if cancel_event and cancel_event.is_set():
                        yield {"type": "cancelled", "content": "Step cancelled"}
                        return full_output

                    if hasattr(event, "type"):
                        if event.type == "content_block_delta":
                            if hasattr(event.delta, "text"):
                                collected_text += event.delta.text
                                yield {"type": "content", "content": event.delta.text}

                # Get the final message for tool_use blocks
                response = stream.get_final_message()

        except Exception as e:
            log.error("Anthropic stream failed: %s", e)
            yield {"type": "error", "content": f"Anthropic API error: {type(e).__name__}: {e}"}
            return full_output

        full_output += collected_text

        # Emit token usage for cost tracking
        if hasattr(response, "usage") and response.usage:
            yield calculate_cost(model, response.usage.input_tokens, response.usage.output_tokens)

        # Check for tool_use blocks
        tool_uses = [block for block in response.content if block.type == "tool_use"]

        if not tool_uses:
            log.info("Anthropic step complete (no tool calls)")
            break

        # Build assistant message with all content blocks
        messages.append({"role": "assistant", "content": response.content})

        # Execute tools and build tool_result message
        tool_results = []
        for tu in tool_uses:
            if cancel_event and cancel_event.is_set():
                yield {"type": "cancelled", "content": "Step cancelled"}
                return full_output

            # ── Input Guardrails ─────────────────────────────────────
            if guardrail_engine:
                violations = guardrail_engine.check_input(tu.name, tu.input)
                for v in violations:
                    yield {"type": "guardrail_violation", "guardrail": v.guardrail_name,
                           "severity": v.severity, "message": v.message}
                if guardrail_engine.has_blocking_violation(violations):
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tu.id,
                        "content": f"Guardrail blocked: {violations[0].message}",
                    })
                    continue

            yield {"type": "tool_call", "name": tu.name, "args": tu.input}
            log.info("Tool call: %s(%s)", tu.name, tu.input)

            # ── Execute with escalation handling ─────────────────────
            from forge.tools.escalation import EscalationError
            try:
                result = registry.execute(tu.name, tu.input, sandbox_path=sandbox_path)
            except EscalationError as esc:
                yield {"type": "escalation", "reason": esc.reason,
                       "category": esc.category, "context": esc.context}
                return full_output

            # ── Generative UI interception ────────────────────────────
            from forge.generative_ui import intercept_widget_result
            widget_event, result = intercept_widget_result(result)
            if widget_event:
                yield widget_event

            # ── Output Guardrails ────────────────────────────────────
            if guardrail_engine:
                out_violations = guardrail_engine.check_output(result)
                for v in out_violations:
                    yield {"type": "guardrail_violation", "guardrail": v.guardrail_name,
                           "severity": v.severity, "message": v.message}

            display_result = result[:500] + "..." if len(result) > 500 else result
            yield {"type": "tool_result", "name": tu.name, "result": display_result}

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": result,
            })

        messages.append({"role": "user", "content": tool_results})
    else:
        yield {"type": "status", "content": f"Hit max iterations ({max_iterations})"}

    return full_output


# ── OpenAI Adapter ─────────────────────────────────────────────────────────

def run_openai(
    model: str,
    system_prompt: str,
    user_prompt: str,
    registry: ToolRegistry,
    sandbox_path: str,
    cancel_event: threading.Event | None,
    max_iterations: int,
    base_url: str | None = None,
    api_key: str | None = None,
    tool_filter: set[str] | None = None,
    task_goal: str = "",
    guardrail_engine=None,
) -> Generator[dict, None, str]:
    """Run OpenAI Chat Completions API with tool-calling loop."""
    try:
        from openai import OpenAI
    except ImportError:
        yield {"type": "error", "content": "openai package not installed. Run: pip install openai"}
        return ""

    effective_key = api_key or _effective_key("openai", OPENAI_API_KEY)
    if not effective_key and not base_url:
        yield {"type": "error", "content": "OPENAI_API_KEY not set in .env"}
        return ""

    REMINDER_INTERVAL = 3

    # LM Studio doesn't need a real key but the SDK requires one
    client_kwargs = {}
    if base_url:
        client_kwargs["base_url"] = base_url
        client_kwargs["api_key"] = effective_key or "lm-studio"
    else:
        client_kwargs["api_key"] = effective_key

    client = OpenAI(**client_kwargs)
    tools = _to_openai_tools(registry, only=tool_filter)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    full_output = ""

    for iteration in range(max_iterations):
        if cancel_event and cancel_event.is_set():
            yield {"type": "cancelled", "content": "Step cancelled"}
            return full_output

        # ── Instruction Reminder ─────────────────────────────────────
        if task_goal and iteration > 0 and iteration % REMINDER_INTERVAL == 0:
            from forge.executor import _current_timestamp
            messages.append({"role": "user", "content":
                f"[SYSTEM REMINDER] {_current_timestamp()}\n"
                f"Original task goal: {task_goal[:500]}\nStay focused."})

        log.info("OpenAI iteration %d (model: %s, base_url: %s)", iteration + 1, model, base_url or "default")

        try:
            # Stream the response
            collected_text = ""
            tool_calls_acc: dict[int, dict] = {}  # index → {id, name, arguments}
            usage_data = None

            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools if tools else None,
                stream=True,
                stream_options={"include_usage": True},
            )

            for chunk in stream:
                if cancel_event and cancel_event.is_set():
                    yield {"type": "cancelled", "content": "Step cancelled"}
                    return full_output

                choice = chunk.choices[0] if chunk.choices else None
                if not choice:
                    continue

                delta = choice.delta
                if delta and delta.content:
                    collected_text += delta.content
                    yield {"type": "content", "content": delta.content}

                # Capture usage from final chunk
                if hasattr(chunk, "usage") and chunk.usage:
                    usage_data = chunk.usage

                # Accumulate streamed tool calls
                if delta and delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": tc_delta.id or "",
                                "name": tc_delta.function.name if tc_delta.function and tc_delta.function.name else "",
                                "arguments": "",
                            }
                        if tc_delta.id:
                            tool_calls_acc[idx]["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                tool_calls_acc[idx]["name"] = tc_delta.function.name
                            if tc_delta.function.arguments:
                                tool_calls_acc[idx]["arguments"] += tc_delta.function.arguments

        except Exception as e:
            log.error("OpenAI stream failed: %s", e)
            yield {"type": "error", "content": f"OpenAI API error: {type(e).__name__}: {e}"}
            return full_output

        full_output += collected_text

        # Emit token usage for cost tracking
        if usage_data:
            in_tok = getattr(usage_data, "prompt_tokens", 0) or 0
            out_tok = getattr(usage_data, "completion_tokens", 0) or 0
            yield calculate_cost(model, in_tok, out_tok)

        # No tool calls → done
        if not tool_calls_acc:
            log.info("OpenAI step complete (no tool calls)")
            break

        # Build assistant message with tool calls
        tool_calls_list = []
        for idx in sorted(tool_calls_acc.keys()):
            tc = tool_calls_acc[idx]
            tool_calls_list.append({
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": tc["arguments"]},
            })

        assistant_msg = {"role": "assistant", "content": collected_text or None, "tool_calls": tool_calls_list}
        messages.append(assistant_msg)

        # Execute each tool call
        for tc in tool_calls_list:
            if cancel_event and cancel_event.is_set():
                yield {"type": "cancelled", "content": "Step cancelled"}
                return full_output

            func_name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                args = {}

            # ── Input Guardrails ─────────────────────────────────────
            if guardrail_engine:
                violations = guardrail_engine.check_input(func_name, args)
                for v in violations:
                    yield {"type": "guardrail_violation", "guardrail": v.guardrail_name,
                           "severity": v.severity, "message": v.message}
                if guardrail_engine.has_blocking_violation(violations):
                    messages.append({
                        "role": "tool", "tool_call_id": tc["id"],
                        "content": f"Guardrail blocked: {violations[0].message}",
                    })
                    continue

            yield {"type": "tool_call", "name": func_name, "args": args}
            log.info("Tool call: %s(%s)", func_name, args)

            # ── Execute with escalation handling ─────────────────────
            from forge.tools.escalation import EscalationError
            try:
                result = registry.execute(func_name, args, sandbox_path=sandbox_path)
            except EscalationError as esc:
                yield {"type": "escalation", "reason": esc.reason,
                       "category": esc.category, "context": esc.context}
                return full_output

            # ── Generative UI interception ────────────────────────────
            from forge.generative_ui import intercept_widget_result
            widget_event, result = intercept_widget_result(result)
            if widget_event:
                yield widget_event

            # ── Output Guardrails ────────────────────────────────────
            if guardrail_engine:
                out_violations = guardrail_engine.check_output(result)
                for v in out_violations:
                    yield {"type": "guardrail_violation", "guardrail": v.guardrail_name,
                           "severity": v.severity, "message": v.message}

            display_result = result[:500] + "..." if len(result) > 500 else result
            yield {"type": "tool_result", "name": func_name, "result": display_result}

            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })
    else:
        yield {"type": "status", "content": f"Hit max iterations ({max_iterations})"}

    return full_output
