"""NES Arena tools — let agents inspect and coach live emulator sessions.

The emulator itself runs in the browser; these tools are read/coach-only.
The browser is the source of truth for button presses — agents coach via
`nes_coach_plan`, and the frontend polls that plan and translates it into
inputs.

Tool surface (mirrors MCP namespace `nes:*`):

  * nes_list_roms        → enumerate .nes titles under FORGE_NES_ROMS_DIR
  * nes_list_sessions    → every active emulator session (browser-backed)
  * nes_get_session      → latest state snapshot (score, lives, last frame)
  * nes_coach_plan       → ask the coach model for a new strategic plan
  * nes_log_event        → record an event (death, pickup, level) + vault

Keep outputs compact — agents often chain these with other tools and we
don't want ticker-tape spam in the executor transcript.
"""
from __future__ import annotations

import json
from typing import Any

from forge import nes_arena as _nes
from .registry import ToolRegistry


def nes_list_roms() -> str:
    """List every NES ROM indexed by the Forge.

    Returns a JSON array of `{slug, title, filename, size_bytes}`. The `slug`
    is the stable id used by other nes_* tools and by the browser loader.
    """
    roms = _nes.list_roms()
    # Keep the agent-facing payload small — drop path_rel.
    trimmed = [
        {"slug": r["slug"], "title": r["title"],
         "filename": r["filename"], "size_kb": round(r["size_bytes"] / 1024, 1)}
        for r in roms
    ]
    return json.dumps({"count": len(trimmed), "roms": trimmed}, indent=2)


def nes_list_sessions() -> str:
    """List every active NES session with its mode, ROM, and latest score."""
    sessions = _nes.list_sessions()
    out = [
        {
            "id": s["id"], "rom_title": s["rom_title"], "mode": s["mode"],
            "coach_model": s["coach_model"],
            "last_score": s["last_score"], "last_lives": s["last_lives"],
            "last_level": s["last_level"], "last_frame_n": s["last_frame_n"],
            "api_calls": s["api_calls"], "cost_usd": s["cost_usd"],
        }
        for s in sessions
    ]
    return json.dumps({"count": len(out), "sessions": out}, indent=2)


def nes_get_session(session_id: str) -> str:
    """Return the full state snapshot for one session (no frame bytes)."""
    s = _nes.get_session(session_id)
    if s is None:
        return json.dumps({"error": f"Unknown session: {session_id!r}"})
    summary = s.summary()
    # Strip the heavy frame data if it crept in (it doesn't currently, but
    # be defensive — agent context is expensive).
    summary.pop("last_frame_b64", None)
    return json.dumps(summary, indent=2)


def nes_coach_plan(
    session_id: str,
    extra_context: str = "",
    model: str = "",
) -> str:
    """Ask the coach model to produce a new plan for an active session.

    Uses the session's most recent frame. Vision-capable models see the
    screen; others get a textual state summary. `extra_context` appends
    free-form text to the prompt (e.g., "we just died to a hammer bro").
    """
    s = _nes.get_session(session_id)
    if s is None:
        return json.dumps({"error": f"Unknown session: {session_id!r}"})

    plan_history = [p.text for p in s.plan_history]
    try:
        result = _nes.coach_advise(
            model=model or s.coach_model,
            rom_title=s.rom_title,
            mode=s.mode,
            frame_b64=s.last_frame_b64,
            plan_history=plan_history,
            score=s.last_score,
            lives=s.last_lives,
            level=s.last_level,
            extra_context=extra_context,
        )
    except RuntimeError as e:
        return json.dumps({"error": str(e)})

    from forge.nes_arena.session import CoachPlan
    from datetime import datetime, timezone
    plan = CoachPlan(
        text=result["plan"],
        emitted_at=datetime.now(timezone.utc).isoformat(),
        model=result["model"],
        ms=result["ms"],
        frame_n=s.last_frame_n,
        raw_response=result["raw"],
    )
    s.set_plan(plan)
    return json.dumps({
        "plan": plan.text,
        "model": plan.model,
        "ms": plan.ms,
        "used_vision": result.get("used_vision", False),
    }, indent=2)


def nes_log_event(
    session_id: str,
    kind: str,
    summary: str,
    frame_n: int = 0,
    extra_json: str = "",
) -> str:
    """Deposit an in-game event. `kind` = death|score|level|powerup|note.

    Also writes to forge:vault under the ns `nes:<rom_slug>` so future
    Grok runs on the same ROM can recall "we died to X at Y".
    """
    s = _nes.get_session(session_id)
    if s is None:
        return json.dumps({"error": f"Unknown session: {session_id!r}"})

    extra: dict = {}
    if extra_json:
        try:
            parsed = json.loads(extra_json)
            if isinstance(parsed, dict):
                extra = parsed
        except json.JSONDecodeError:
            extra = {"raw": extra_json[:200]}

    from forge.nes_arena.session import NESEvent
    event = NESEvent(
        kind=kind, summary=summary,
        frame_n=frame_n or s.last_frame_n, extra=extra,
    )
    s.add_event(event)

    status = _nes.log_event(
        session_id=session_id,
        rom_slug=s.rom_slug,
        rom_title=s.rom_title,
        kind=kind,
        summary=summary,
        frame_n=event.frame_n,
        extra=extra,
    )
    return json.dumps({"ok": True, **status})


def register(registry: ToolRegistry) -> None:
    registry.register(
        name="nes_list_roms",
        description=(
            "List every NES ROM indexed by the Forge. Returns slug + title + "
            "filename + size. Use the slug with the browser UI to boot a game."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        handler=nes_list_roms,
    )
    registry.register(
        name="nes_list_sessions",
        description=(
            "List every active NES emulator session (the browser tells us when "
            "one boots). Returns id, ROM, mode, latest score/lives/level."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        handler=nes_list_sessions,
    )
    registry.register(
        name="nes_get_session",
        description=(
            "Snapshot one NES session: current plan, event log, cost counter, "
            "latest frame number. Does NOT return the frame bytes — use the "
            "browser canvas for that."
        ),
        parameters={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "12-hex session id"},
            },
            "required": ["session_id"],
        },
        handler=nes_get_session,
    )
    registry.register(
        name="nes_coach_plan",
        description=(
            "Ask the coach model to produce a new strategic plan for an active "
            "session. The session's last frame is sent automatically if the "
            "model supports vision."
        ),
        parameters={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "extra_context": {
                    "type": "string",
                    "description": "Optional free-form context appended to the prompt.",
                },
                "model": {
                    "type": "string",
                    "description": "Override coach model (empty = session default).",
                },
            },
            "required": ["session_id"],
        },
        handler=nes_coach_plan,
    )
    registry.register(
        name="nes_log_event",
        description=(
            "Record an in-game event and deposit to forge:vault under "
            "nes:<rom_slug>. Use kind=death|score|level|powerup|note. "
            "Future runs can recall these breadcrumbs."
        ),
        parameters={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "kind":       {"type": "string"},
                "summary":    {"type": "string"},
                "frame_n":    {"type": "integer"},
                "extra_json": {
                    "type": "string",
                    "description": "Optional JSON string with structured extras.",
                },
            },
            "required": ["session_id", "kind", "summary"],
        },
        handler=nes_log_event,
    )
