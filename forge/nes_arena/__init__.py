"""
NES Arena — LLM-as-coach for classic NES games.

The emulator itself (`jsnes`) runs in the browser canvas. This Python side
handles everything that needs the Forge's auth / providers / vault:
  * ROM library indexing (scan forge/nes/)
  * Coach-model calls (send a frame + game state, get a high-level plan)
  * Vault sync (deposit "died to hammer-bro at 1-1 frame 1847" breadcrumbs)
  * Session registry (so MCP tools can refer to active sessions by id)

Architecture (Mode B: Hybrid from the design brief):
  * COACH — big reasoning model (Grok 4.20 / Claude Sonnet). Called every
    N seconds with a frame + score/lives/level. Returns strategic goals
    ("go right, grab the mushroom, jump the next Koopa").
  * CONTROLLER — fast model (or in v1, any non-reasoning model). Called
    more frequently (~1-2 Hz) with the current frame + coach plan. Returns
    the next button combination (UP, DOWN, LEFT, RIGHT, A, B, SELECT, START).

v1 collapses both roles into one model call per tick — the architecture is
preserved so a local VLM can slot in as a separate fast controller later.
"""
from .rom_index import list_roms, get_rom_bytes, rom_by_slug
from .session import NESSession
from .registry import (
    create_session, get_session, list_sessions, delete_session,
)
from .coach import coach_advise
from .vault_sync import log_event

__all__ = [
    "list_roms", "get_rom_bytes", "rom_by_slug",
    "NESSession",
    "create_session", "get_session", "list_sessions", "delete_session",
    "coach_advise", "log_event",
]
