"""
NESSession — server-side shadow of a live emulator session in the browser.

The actual emulator (jsnes) runs in the client's canvas, so this object
mostly tracks metadata and rolling summaries: which ROM is loaded, who's
playing (human / grok / grok-vs-grok), the most recent coach plan, a ring
buffer of recent events (deaths, score jumps, level changes), and running
cost bookkeeping for the coach/controller API calls.

The browser POSTs tick data (frame screenshot, RAM-ish snapshot, score,
lives) to /api/nes/... and we stuff it into the session here. MCP tools
then read this same state when Grok wants to "look at what's happening."
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Optional

log = logging.getLogger("forge.nes_arena.session")


@dataclass
class CoachPlan:
    """High-level strategic directive from the coach model."""
    text: str
    emitted_at: str       # ISO timestamp
    model: str
    ms: int               # wall-clock of the call
    frame_n: int          # which session frame was shown to the coach
    raw_response: str = ""


@dataclass
class NESEvent:
    """Significant in-game moment worth gossiping about (and vault-syncing)."""
    kind: str             # "death" | "score" | "level" | "powerup" | "note"
    frame_n: int
    summary: str
    extra: dict = field(default_factory=dict)
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class NESSession:
    id: str
    rom_slug: str
    rom_title: str
    mode: str             # "human" | "grok" | "grok-vs-grok" | "hybrid-coach"
    coach_model: str
    controller_model: str
    coach_interval_ms: int = 2000
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Live state snapshots pushed by the browser
    last_frame_b64: str = ""           # most recent PNG (base64 data URL) of the canvas
    last_frame_n: int = 0
    last_frame_at: Optional[str] = None

    # RAM-shaped derivatives the browser sends us (game-specific; jsnes
    # exposes `cpu.mem` as a 64K array — browser picks interesting slices).
    last_score: int = 0
    last_lives: int = 0
    last_level: str = ""
    last_ram_snapshot: dict = field(default_factory=dict)

    # Coach state
    current_plan: Optional[CoachPlan] = None
    plan_history: Deque[CoachPlan] = field(default_factory=lambda: deque(maxlen=12))

    # Event ring buffer (surfaces to UI + vault)
    events: Deque[NESEvent] = field(default_factory=lambda: deque(maxlen=64))

    # Running session cost (coach/controller model API calls)
    cost_usd: float = 0.0
    api_calls: int = 0

    # Concurrency
    lock: threading.Lock = field(default_factory=threading.Lock)

    def summary(self) -> dict:
        """JSON-safe snapshot for /api/nes/sessions/<id>."""
        with self.lock:
            return {
                "id": self.id,
                "rom_slug": self.rom_slug,
                "rom_title": self.rom_title,
                "mode": self.mode,
                "coach_model": self.coach_model,
                "controller_model": self.controller_model,
                "coach_interval_ms": self.coach_interval_ms,
                "created_at": self.created_at,
                "last_frame_n": self.last_frame_n,
                "last_frame_at": self.last_frame_at,
                "last_score": self.last_score,
                "last_lives": self.last_lives,
                "last_level": self.last_level,
                "current_plan": (
                    {
                        "text": self.current_plan.text,
                        "emitted_at": self.current_plan.emitted_at,
                        "model": self.current_plan.model,
                        "ms": self.current_plan.ms,
                        "frame_n": self.current_plan.frame_n,
                    } if self.current_plan else None
                ),
                "plan_history": [
                    {"text": p.text, "emitted_at": p.emitted_at, "model": p.model, "ms": p.ms}
                    for p in list(self.plan_history)
                ],
                "events": [
                    {"kind": e.kind, "frame_n": e.frame_n, "summary": e.summary,
                     "extra": e.extra, "at": e.at}
                    for e in list(self.events)
                ],
                "cost_usd": round(self.cost_usd, 6),
                "api_calls": self.api_calls,
            }

    def ingest_tick(self, frame_b64: str, frame_n: int, state: dict) -> None:
        """Browser → server heartbeat. state may include score/lives/level."""
        with self.lock:
            if frame_b64:
                self.last_frame_b64 = frame_b64
            self.last_frame_n = frame_n
            self.last_frame_at = datetime.now(timezone.utc).isoformat()
            if isinstance(state, dict):
                if "score" in state:  self.last_score = int(state["score"])
                if "lives" in state:  self.last_lives = int(state["lives"])
                if "level" in state:  self.last_level = str(state["level"])
                if "ram" in state and isinstance(state["ram"], dict):
                    # Keep the slice small so we don't blow up memory
                    self.last_ram_snapshot = dict(list(state["ram"].items())[:64])

    def set_plan(self, plan: CoachPlan) -> None:
        with self.lock:
            self.current_plan = plan
            self.plan_history.append(plan)

    def add_event(self, event: NESEvent) -> None:
        with self.lock:
            self.events.append(event)

    def add_cost(self, usd: float) -> None:
        with self.lock:
            self.cost_usd += float(usd or 0.0)
            self.api_calls += 1
