"""
Task State Store — small SQLite-backed registry of in-flight tasks.

Why this exists
---------------
The Flask harness keeps `task_queues`, `task_results`, `task_cancel_events`,
`task_costs`, and per-task cost in process-local dicts. That works fine for
a single-process dev server but creates two real problems:

  1. Restart loses every in-flight task with no audit trail. A user sees
     their task vanish from the UI mid-run with no idea what happened.
  2. There is no durable record of *which* tasks were running when the
     server went down — so you can't recover, you can't tell the user
     "your task was interrupted by a restart," and you can't even count
     how many crashes happened in a window.

This module is intentionally minimal: one table, one connection per call,
no migrations. It coexists with the in-memory dicts (those still serve
live SSE) — this just records lifecycle transitions so they survive restarts.

Schema
------
tasks(
    task_id           TEXT PRIMARY KEY,
    task              TEXT NOT NULL,
    status            TEXT NOT NULL,            -- queued|running|done|error|cancelled|interrupted
    pack              TEXT,
    executor_model    TEXT,
    direct_mode       INTEGER,
    sandbox_path      TEXT,
    cost_usd          REAL DEFAULT 0,
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL,
    finished_at       REAL,
    error             TEXT
)

Restart recovery
----------------
On import, `mark_orphans_interrupted()` flips any rows still in
status='running' or 'queued' to 'interrupted'. The next page load will
show them as such instead of "stuck running forever."
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from forge.config import DATA_DIR

log = logging.getLogger("forge.task_state")

DB_PATH = DATA_DIR / "task_state.db"

# SQLite write lock — one writer at a time. The dev server is single-process
# so this is just defense in depth; production WSGI may call into us from
# multiple worker threads.
_db_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    # WAL gives us concurrent readers without blocking writers.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def _writer() -> Iterator[sqlite3.Connection]:
    """Serialize writes through a single lock."""
    with _db_lock:
        conn = _connect()
        try:
            yield conn
        finally:
            conn.close()


def _init_schema() -> None:
    with _writer() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id        TEXT PRIMARY KEY,
                task           TEXT NOT NULL,
                status         TEXT NOT NULL,
                pack           TEXT,
                executor_model TEXT,
                direct_mode    INTEGER DEFAULT 0,
                sandbox_path   TEXT,
                cost_usd       REAL DEFAULT 0,
                created_at     REAL NOT NULL,
                updated_at     REAL NOT NULL,
                finished_at    REAL,
                error          TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC)"
        )


def mark_orphans_interrupted() -> int:
    """At server startup, mark tasks that were 'running' or 'queued' as
    'interrupted'. Returns the count flipped — useful for a restart-banner.
    """
    now = time.time()
    with _writer() as conn:
        cur = conn.execute(
            """
            UPDATE tasks
               SET status      = 'interrupted',
                   updated_at  = ?,
                   finished_at = ?,
                   error       = COALESCE(error,
                                          'Server restarted while task was in flight')
             WHERE status IN ('queued', 'running')
            """,
            (now, now),
        )
        n = cur.rowcount or 0
    if n:
        log.warning("Marked %d in-flight task(s) as interrupted on startup", n)
    return n


def record_submitted(
    task_id: str,
    task: str,
    *,
    pack: str = "",
    executor_model: str = "",
    direct_mode: bool = False,
    sandbox_path: str = "",
) -> None:
    """Called when a task is queued from the API."""
    now = time.time()
    with _writer() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO tasks (
                task_id, task, status, pack, executor_model,
                direct_mode, sandbox_path, cost_usd,
                created_at, updated_at
            ) VALUES (?, ?, 'queued', ?, ?, ?, ?, 0, ?, ?)
            """,
            (task_id, task, pack, executor_model, int(direct_mode),
             sandbox_path, now, now),
        )


def update_status(
    task_id: str,
    status: str,
    *,
    cost_usd: float | None = None,
    error: str | None = None,
) -> None:
    """Update a task's status. Use 'running', 'done', 'error', 'cancelled'."""
    now = time.time()
    fields = ["status = ?", "updated_at = ?"]
    values: list = [status, now]
    if cost_usd is not None:
        fields.append("cost_usd = ?")
        values.append(round(cost_usd, 6))
    if error is not None:
        fields.append("error = ?")
        values.append(error[:1000])
    if status in ("done", "error", "cancelled", "interrupted"):
        fields.append("finished_at = ?")
        values.append(now)
    values.append(task_id)
    with _writer() as conn:
        conn.execute(
            f"UPDATE tasks SET {', '.join(fields)} WHERE task_id = ?",
            values,
        )


def get_task(task_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_recent(limit: int = 50, status: str | None = None) -> list[dict]:
    """Return recent tasks, newest first, optionally filtered by status."""
    conn = _connect()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def counts_by_status() -> dict[str, int]:
    """Diagnostic — row counts grouped by status. Cheap; safe to expose
    on /api/health."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}
    finally:
        conn.close()


# Initialize on import — creates the table if missing, no-op otherwise.
_init_schema()
