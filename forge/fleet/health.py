"""
Provider health + content-shape text extraction for fleet dispatch.

Reuses ``forge.providers`` health cache and ``anthropic_message_text`` so we
do not reimplement multi-provider chat stacks (spec §5, §10).
"""
from __future__ import annotations

import logging
from typing import Any

from forge.providers import (
    anthropic_message_text,
    health_snapshot,
    is_provider_healthy as _providers_is_healthy,
)

log = logging.getLogger("forge.fleet.health")

__all__ = [
    "anthropic_message_text",
    "extract_text",
    "is_provider_healthy",
    "health_snapshot",
    "mark_provider_unhealthy",
    "clear_provider_health",
]


def _norm_key(provider: str) -> str:
    from forge.fleet.registry import normalize_provider

    return normalize_provider(provider)


def is_provider_healthy(provider: str) -> tuple[bool, str]:
    """Health check with normalized provider keys."""
    return _providers_is_healthy(_norm_key(provider))


def extract_text(content: Any) -> str:
    """Content-shape-agnostic text extraction.

    Never assumes a single content[0] text block. Handles:
      - plain str / None
      - Anthropic content block lists (incl. ThinkingBlock salvage)
      - OpenAI-style message objects with ``.content``
      - list of dicts with ``type``/``text``/``thinking``
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()

    if hasattr(content, "content") and not isinstance(content, (list, dict)):
        return extract_text(getattr(content, "content"))

    if isinstance(content, list):
        # Sniff first few blocks for object-style Anthropic shapes
        sample = content[:3]
        if any(hasattr(b, "type") for b in sample if b is not None):
            return anthropic_message_text(content)
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
                continue
            if isinstance(block, dict):
                btype = block.get("type") or ""
                if btype == "text" and block.get("text"):
                    text_parts.append(str(block["text"]))
                elif btype in ("thinking", "redacted_thinking"):
                    t = block.get("thinking") or block.get("text")
                    if t:
                        thinking_parts.append(str(t))
                elif block.get("text") and btype != "tool_use":
                    text_parts.append(str(block["text"]))
                continue
            t = getattr(block, "text", None)
            if t:
                text_parts.append(str(t))
        if text_parts:
            return "\n".join(text_parts).strip()
        if thinking_parts:
            return "\n".join(thinking_parts).strip()
        return ""

    if isinstance(content, dict):
        if "text" in content:
            return str(content.get("text") or "").strip()
        if "content" in content:
            return extract_text(content["content"])
        return ""

    return str(content).strip()


def mark_provider_unhealthy(provider: str, reason: str | BaseException) -> None:
    """Mark a provider unhealthy (cooldown) via the shared providers cache."""
    from forge import providers as _prov

    key = _norm_key(provider)
    if isinstance(reason, BaseException):
        exc: BaseException = reason
    else:
        exc = RuntimeError(str(reason))
    _prov._mark_provider_unhealthy(key, exc)


def clear_provider_health(provider: str | None = None) -> None:
    """Clear health state (tests / recovery). ``None`` clears all."""
    from forge import providers as _prov

    with _prov._provider_health_lock:
        if provider is None:
            _prov._provider_unhealthy_until.clear()
        else:
            key = _norm_key(provider)
            _prov._provider_unhealthy_until.pop(key, None)
            # Also drop any non-normalized legacy keys that match casefold
            for k in list(_prov._provider_unhealthy_until.keys()):
                if _norm_key(k) == key:
                    _prov._provider_unhealthy_until.pop(k, None)
