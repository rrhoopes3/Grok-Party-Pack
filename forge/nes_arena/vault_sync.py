"""
NES → Vault deposit hook.

When something interesting happens (death, level-up, high score), the
browser POSTs to /api/nes/sessions/<id>/event. We record it on the session
and, if the MCP router is online, also deposit it to forge:vault under
the nes:* namespace so Grok remembers "last time we played this level we
died to the hammer bro at frame 1847."

Deposits are best-effort — a MCP router failure never breaks the session.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger("forge.nes_arena.vault_sync")


def _format_vault_entry(rom_title: str, event_kind: str, summary: str,
                        frame_n: int, extra: dict) -> str:
    """Human-readable line that reads well when surfaced in vault search."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    extras = ""
    if extra:
        pairs = ", ".join(f"{k}={v}" for k, v in list(extra.items())[:4])
        extras = f"  [{pairs}]"
    return f"[{ts}] {rom_title} · {event_kind}: {summary} (frame {frame_n}){extras}"


def log_event(
    session_id: str,
    rom_slug: str,
    rom_title: str,
    kind: str,
    summary: str,
    frame_n: int = 0,
    extra: dict | None = None,
) -> dict:
    """
    Deposit an NES event to the session's event log + MCP vault.
    Returns a status dict — vault deposit status is best-effort.
    """
    extra = extra or {}
    entry = _format_vault_entry(rom_title, kind, summary, frame_n, extra)

    vault_ok = False
    vault_msg = "mcp disabled"
    try:
        from forge.mcp_client import get_router
        router = get_router()
        namespace = f"nes:{rom_slug}"
        # Most MCP routers expose `deposit` / `vault.append` style calls; we
        # try a couple of common shapes and degrade gracefully.
        for attempt in ("vault_deposit", "deposit", "note"):
            fn = getattr(router, attempt, None)
            if callable(fn):
                try:
                    fn(namespace=namespace, text=entry, meta={
                        "session_id": session_id, "kind": kind,
                        "frame_n": frame_n, **extra,
                    })
                    vault_ok = True
                    vault_msg = f"via router.{attempt}"
                    break
                except Exception as inner:
                    vault_msg = f"router.{attempt} failed: {inner}"
        if not vault_ok and vault_msg.startswith("mcp"):
            vault_msg = "router has no deposit method"
    except Exception as e:
        vault_msg = f"router unavailable: {type(e).__name__}: {e}"

    if vault_ok:
        log.info("vault deposit ok: %s", entry)
    else:
        log.debug("vault deposit skipped (%s): %s", vault_msg, entry)

    return {
        "entry": entry,
        "vault_ok": vault_ok,
        "vault_msg": vault_msg,
    }
