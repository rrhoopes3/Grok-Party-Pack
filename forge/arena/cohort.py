"""Cohort players for Conquest.

A cohort is anything that is not a model API: another Grok Bot, a file drop,
or a subprocess. It speaks the same JSON the engine already validates.

Request (one per order or duel), sent as a dict or one NDJSON line::

    {"id": 1, "kind": "orders"|"duel", "faction": "red", "turn": 1,
     "prompt": "<same text a model sees>", "system": "<referee rules>",
     "view": { ... fog-safe map, only on what this faction can see ... }}

Response::

    {"id": 1, "body": { ...the order or duel JSON... }}

``body`` is exactly what a model would have replied with. The engine checks it.
A missing or late reply forfeits that decision (empty orders / no duel answer).

Wire formats
- ``HeuristicCohort`` — in-process script. No keys.
- ``FileCohort(dir)`` — writes ``request.json``, waits for ``response.json``.
- ``SubprocessCohort(argv)`` — one NDJSON request per line on stdin, one
  response per line on stdout. Logs go to stderr, never stdout.
"""
from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from forge.arena.cohort_bot import HeuristicCohort

__all__ = [
    "HeuristicCohort",
    "FileCohort",
    "SubprocessCohort",
    "parse_controller",
]


class FileCohort:
    """One faction, one directory. Safe for a teammate that wakes up per turn."""

    def __init__(self, directory: str | Path, timeout: float = 120.0, poll: float = 0.05):
        self.directory = Path(directory)
        self.timeout = timeout
        self.poll = poll
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def __call__(self, req: dict) -> dict:
        with self._lock:
            rid = uuid.uuid4().hex
            request_path = self.directory / "request.json"
            response_path = self.directory / "response.json"
            response_path.unlink(missing_ok=True)
            payload = dict(req)
            payload["id"] = rid
            tmp = request_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, request_path)
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                if response_path.exists():
                    try:
                        data = json.loads(response_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        time.sleep(self.poll)
                        continue
                    if isinstance(data, dict) and data.get("id") == rid:
                        response_path.unlink(missing_ok=True)
                        return data
                    # Stale file from a previous turn. Drop it and keep waiting.
                    response_path.unlink(missing_ok=True)
                time.sleep(self.poll)
            return {}

    def close(self) -> None:
        return None


class SubprocessCohort:
    """Long-running controller. One process per faction (duels run in parallel)."""

    def __init__(self, argv: list[str], timeout: float = 30.0, cwd: str | Path | None = None):
        self.timeout = timeout
        self._seq = 0
        self._lock = threading.Lock()
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            bufsize=1,
            cwd=str(cwd) if cwd else None,
        )

    def __call__(self, req: dict) -> dict:
        with self._lock:
            if self.proc.poll() is not None or self.proc.stdin is None or self.proc.stdout is None:
                return {}
            self._seq += 1
            rid = self._seq
            payload = dict(req)
            payload["id"] = rid
            try:
                self.proc.stdin.write(json.dumps(payload) + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError):
                return {}
            line = self._readline()
            if not line:
                return {}
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                return {}
            if not isinstance(data, dict) or data.get("id") != rid:
                return {}
            return data

    def _readline(self) -> str:
        assert self.proc.stdout is not None
        ready, _, _ = select.select([self.proc.stdout], [], [], self.timeout)
        if not ready:
            self.proc.kill()
            return ""
        return self.proc.stdout.readline()

    def close(self) -> None:
        if self.proc.poll() is None and self.proc.stdin is not None:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def parse_controller(
    spec: str, timeout: float, cwd: str | Path | None = None,
) -> Callable[[dict], Any]:
    """``heuristic``, ``subprocess``, ``file:<dir>``, or ``cmd:<argv>``."""
    if spec == "heuristic":
        return HeuristicCohort()
    if spec == "subprocess":
        return SubprocessCohort(
            [sys.executable, "-m", "forge.arena.cohort_bot"],
            timeout=timeout,
            cwd=cwd,
        )
    if spec.startswith("file:"):
        return FileCohort(spec[5:], timeout=timeout)
    if spec.startswith("cmd:"):
        import shlex
        argv = shlex.split(spec[4:])
        if not argv:
            raise SystemExit("cmd: needs a command")
        return SubprocessCohort(argv, timeout=timeout, cwd=cwd)
    raise SystemExit(
        f"unknown cohort spec {spec!r} "
        "(heuristic | subprocess | file:<dir> | cmd:<command>)"
    )
