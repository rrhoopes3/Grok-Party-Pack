"""
In-memory registry of active NES sessions.

Sessions are cheap (no subprocess, just state) — we cap at 8 concurrent
sessions and evict oldest when exceeded. No persistence across restart.
"""
from __future__ import annotations

import logging
import threading
import uuid
from typing import Optional

from .session import NESSession

log = logging.getLogger("forge.nes_arena.registry")

_SESSIONS: dict[str, NESSession] = {}
_LOCK = threading.Lock()
_MAX_SESSIONS = 8


def create_session(
    rom_slug: str,
    rom_title: str,
    mode: str,
    coach_model: str,
    controller_model: str,
    coach_interval_ms: int = 2000,
) -> NESSession:
    with _LOCK:
        if len(_SESSIONS) >= _MAX_SESSIONS:
            oldest_id = min(_SESSIONS, key=lambda k: _SESSIONS[k].created_at)
            log.info("Evicting oldest NES session: %s", oldest_id)
            _SESSIONS.pop(oldest_id, None)

        s = NESSession(
            id=uuid.uuid4().hex[:12],
            rom_slug=rom_slug,
            rom_title=rom_title,
            mode=mode,
            coach_model=coach_model,
            controller_model=controller_model,
            coach_interval_ms=coach_interval_ms,
        )
        _SESSIONS[s.id] = s
        log.info("NES session created: %s (%s, mode=%s)", s.id, rom_title, mode)
        return s


def get_session(session_id: str) -> Optional[NESSession]:
    return _SESSIONS.get(session_id)


def list_sessions() -> list[dict]:
    with _LOCK:
        items = sorted(_SESSIONS.values(), key=lambda s: s.created_at, reverse=True)
    return [s.summary() for s in items]


def delete_session(session_id: str) -> bool:
    with _LOCK:
        return _SESSIONS.pop(session_id, None) is not None
