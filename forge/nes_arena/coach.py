"""
Coach model — the "high-level strategist" from Mode B.

Called every N seconds with the current frame (as a base64 data URL) plus
the session's observed score/lives/level. Returns a short plan the
controller (or a human) uses as direction for the next few seconds.

Provider selection mirrors prophecy / chess_arena: prefix-route the model
name. Supports vision-capable models (Claude 3.5 Sonnet, GPT-4o, Grok
Vision) — sends the frame as an image part. Falls back to text-only if the
model doesn't take images (strips the frame, sends a verbal summary only).
"""
from __future__ import annotations

import base64
import logging
import re
import time
from typing import Any, Optional

from forge.config import (
    XAI_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY,
    LMSTUDIO_BASE_URL, OLLAMA_BASE_URL,
)

log = logging.getLogger("forge.nes_arena.coach")


# Models known to accept image input. Keep conservative — when in doubt,
# send text only so we don't error out on a bad request.
_VISION_MODELS = (
    "claude-3-5-sonnet", "claude-3-opus",
    # Claude 4 family — all vision-capable at every size
    "claude-sonnet-4", "claude-opus-4",
    "claude-sonnet-4-6", "claude-opus-4-6", "claude-opus-4-7",
    "claude-sonnet-4-5", "claude-opus-4-5", "claude-haiku-4-5",
    "claude-opus-4-1",
    # OpenAI
    "gpt-4o", "gpt-4-vision", "gpt-4-turbo-vision", "o4",
    "gpt-5.4", "gpt-5-4",  # both hyphen and dot spellings so partial-match works
    # Grok vision tiers
    "grok-vision", "grok-2-vision", "grok-4-vision",
)


def _provider_for(model: str) -> str:
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith(("gpt-", "o1-", "o3-", "o4-", "chatgpt-")):
        return "openai"
    if model.startswith("lmstudio:"):
        return "lmstudio"
    if model.startswith("ollama:"):
        return "ollama"
    return "xai"


def _supports_vision(model: str) -> bool:
    m = model.lower()
    return any(marker in m for marker in _VISION_MODELS)


# ────────────────────────────────────────────────────────────────────────
# Prompt construction
# ────────────────────────────────────────────────────────────────────────

_COACH_SYSTEM = (
    "You are the coach for an AI playing a classic NES game. Your job is "
    "high-level strategy, not frame-by-frame inputs. Respond in ONE short "
    "paragraph (2-4 sentences max) describing what the player should do "
    "over the next few seconds. Be specific and actionable — name enemies, "
    "paths, or items on screen. If you see the player about to die, say "
    "so explicitly and prefix your plan with 'DANGER: '. Do not hallucinate "
    "details you can't see; when unsure, default to safe advice."
)


def _build_text_prompt(
    rom_title: str, mode: str, plan_history: list[str],
    score: int, lives: int, level: str,
    extra_context: str = "",
) -> str:
    lines = [
        f"Game: {rom_title}",
        f"Mode: {mode}",
        f"Current score: {score}  Lives: {lives}  Level: {level or '?'}",
    ]
    if plan_history:
        lines.append("Your last 3 plans (most recent last):")
        for i, p in enumerate(plan_history[-3:], 1):
            lines.append(f"  {i}. {p}")
    if extra_context:
        lines.append("")
        lines.append(extra_context)
    lines.append("")
    lines.append("Give the player their next 3-5 second plan.")
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────
# Provider-specific callers
# ────────────────────────────────────────────────────────────────────────

def _strip_data_url_prefix(data_url: str) -> tuple[str, str]:
    """
    'data:image/png;base64,iVBOR...' → ('image/png', 'iVBOR...')
    Accepts bare base64 too, defaulting to image/png.
    """
    m = re.match(r"^data:([^;]+);base64,(.+)$", data_url)
    if m:
        return m.group(1), m.group(2)
    return "image/png", data_url


# Claude 4.5+ and OpenAI reasoning models (o-series, GPT-5 family) deprecate
# the `temperature` kwarg and 400 if sent. Detect by name prefix and drop it.
def _model_rejects_temperature(model: str) -> bool:
    m = model.lower()
    if m.startswith((
        "claude-opus-4-7",
        "claude-opus-4-6", "claude-sonnet-4-6",
        "claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5",
    )):
        return True
    if m.startswith(("o1-", "o3-", "o4-", "gpt-5")):
        return True
    return False


# OpenAI renamed `max_tokens` → `max_completion_tokens` on o-series + GPT-5.
# Anthropic kept max_tokens, so this only matters inside _call_openai_compat.
def _model_uses_max_completion_tokens(model: str) -> bool:
    m = model.lower()
    return m.startswith(("o1-", "o3-", "o4-", "gpt-5"))


def _call_anthropic(prompt: str, system: str, model: str,
                    image_b64: Optional[str]) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    content: list[dict[str, Any]] = []
    if image_b64:
        mime, b64 = _strip_data_url_prefix(image_b64)
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": mime, "data": b64},
        })
    content.append({"type": "text", "text": prompt})

    kwargs: dict[str, Any] = {
        "model": model, "max_tokens": 400, "system": system,
        "messages": [{"role": "user", "content": content}],
    }
    if not _model_rejects_temperature(model):
        kwargs["temperature"] = 0.6
    resp = client.messages.create(**kwargs)
    from forge.providers import anthropic_message_text
    return anthropic_message_text(resp.content)


def _call_openai_compat(prompt: str, system: str, model: str,
                        image_b64: Optional[str],
                        base_url: Optional[str], api_key: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=api_key or "none", base_url=base_url)

    if image_b64:
        mime, b64 = _strip_data_url_prefix(image_b64)
        user_content = [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            {"type": "text", "text": prompt},
        ]
    else:
        user_content = prompt

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_content})

    call_kwargs: dict[str, Any] = {
        "model": model, "messages": messages,
    }
    if _model_uses_max_completion_tokens(model):
        call_kwargs["max_completion_tokens"] = 400
    else:
        call_kwargs["max_tokens"] = 400
    if not _model_rejects_temperature(model):
        call_kwargs["temperature"] = 0.6
    resp = client.chat.completions.create(**call_kwargs)
    return resp.choices[0].message.content or ""


# ────────────────────────────────────────────────────────────────────────
# Public entry point
# ────────────────────────────────────────────────────────────────────────

def coach_advise(
    model: str,
    rom_title: str,
    mode: str,
    frame_b64: Optional[str],
    plan_history: list[str],
    score: int = 0,
    lives: int = 0,
    level: str = "",
    extra_context: str = "",
) -> dict:
    """
    Ask the coach for a plan. Returns:
      { plan: "...", model: "...", ms: 123, used_vision: True, raw: "..." }
    Raises RuntimeError for hard failures (missing API key, upstream 5xx).
    """
    started = time.monotonic()
    use_vision = bool(frame_b64) and _supports_vision(model)
    image = frame_b64 if use_vision else None
    prompt = _build_text_prompt(
        rom_title, mode, plan_history,
        score=score, lives=lives, level=level,
        extra_context=extra_context,
    )

    provider = _provider_for(model)
    try:
        if provider == "anthropic":
            raw = _call_anthropic(prompt, _COACH_SYSTEM, model, image)
        else:
            base_url: Optional[str] = None
            api_key = "none"
            if provider == "openai":
                api_key = OPENAI_API_KEY or ""
            elif provider == "lmstudio":
                base_url = LMSTUDIO_BASE_URL
                api_key = "lm-studio"
                model = model.removeprefix("lmstudio:") or "default"
            elif provider == "ollama":
                base_url = OLLAMA_BASE_URL
                api_key = "ollama"
                model = model.removeprefix("ollama:") or "default"
            else:  # xai
                base_url = "https://api.x.ai/v1"
                api_key = XAI_API_KEY or ""
            raw = _call_openai_compat(prompt, _COACH_SYSTEM, model, image, base_url, api_key)
    except Exception as e:
        raise RuntimeError(f"coach call failed ({model}): {type(e).__name__}: {e}") from e

    plan_text = (raw or "").strip()
    ms = int((time.monotonic() - started) * 1000)
    return {
        "plan": plan_text,
        "model": model,
        "ms": ms,
        "used_vision": use_vision,
        "raw": plan_text,
    }
