"""
Instant-answer short-circuit for trivial system-info queries.

Matches tasks like "determine your PWD", "what directory am I in",
"whoami", "what time is it", etc. and answers them with a direct
subprocess call — zero LLM tokens, sub-100ms.
"""
from __future__ import annotations
import logging
import os
import re
import subprocess
import platform

log = logging.getLogger("forge.instant")

_PATTERNS: list[tuple[re.Pattern, str | callable]] = [
    (re.compile(
        r"\b(pwd|print\s+working\s+dir|current\s+(dir|directory|working\s*dir)"
        r"|where\s+am\s+i|getcwd|what\s+(dir|directory|folder))\b",
        re.IGNORECASE,
    ), "pwd"),
    (re.compile(
        r"\b(whoami|who\s+am\s+i|current\s+user|my\s+username)\b",
        re.IGNORECASE,
    ), "whoami"),
    (re.compile(
        r"\b(hostname|machine\s+name|computer\s+name)\b",
        re.IGNORECASE,
    ), "hostname"),
    (re.compile(
        r"\b(current\s+(time|date)|what\s+(time|date|day)\s+is\s+it"
        r"|today'?s?\s+date)\b",
        re.IGNORECASE,
    ), "date"),
]

# Tasks containing these markers are real work, not instant-answerable
_DISQUALIFIERS = ("```", "http://", "https://", ".py", ".js", ".json", ".md",
                  " and ", " then ", " after ", " also ")


def try_instant_answer(task: str) -> str | None:
    """Return an instant answer string, or None if the task isn't trivial."""
    t = (task or "").strip()
    if not t or len(t) > 150:
        return None
    if any(d in t for d in _DISQUALIFIERS):
        return None

    for pattern, cmd in _PATTERNS:
        if pattern.search(t):
            return _run(cmd)
    return None


def _run(cmd: str) -> str:
    if cmd == "pwd":
        return os.getcwd()
    if cmd == "whoami":
        return _shell("whoami")
    if cmd == "hostname":
        return platform.node()
    if cmd == "date":
        return _shell("date /t" if platform.system() == "Windows" else "date")
    return None


def _shell(cmd: str) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or r.stderr.strip()
    except Exception as e:
        log.warning("Instant shell failed for %r: %s", cmd, e)
        return f"(error: {e})"
