"""
Run Log — persistent event stream per task.

Every SSE event emitted during a task (or arena match) is appended to a
per-task JSONL file in forge/data/runs/<task_id>.jsonl. This enables:
  - Full run replay in the Run Inspector UI
  - Widget/artifact retrieval after session ends
  - Post-hoc analysis of tool calls, guardrail hits, costs, judge scores
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from forge.config import RUNS_DIR
from forge.security import resolve_in_root, safe_id

log = logging.getLogger("forge.run_log")


def _run_file(task_id: str, suffix: str) -> Path | None:
    try:
        tid = safe_id(task_id)
        return resolve_in_root(RUNS_DIR, f"{tid}{suffix}")
    except ValueError:
        return None


class RunLog:
    """Append-only event log for a single task run."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        path = _run_file(task_id, ".jsonl")
        if path is None:
            raise ValueError("invalid task_id")
        self.path = path
        self._artifacts: list[dict] = []
        self._event_count = 0

    def append(self, event: dict):
        """Append a single event to the log file."""
        entry = {
            "t": round(time.time(), 3),
            "seq": self._event_count,
            **event,
        }
        self._event_count += 1

        # Track widget artifacts for quick retrieval
        if event.get("type") == "widget_render":
            self._artifacts.append({
                "seq": entry["seq"],
                "kind": "widget",
                "widget_id": event.get("widget_id", ""),
                "widget_type": event.get("widget_type", ""),
                "title": event.get("title", ""),
            })

        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            log.warning("Failed to write run log event: %s", e)

    def finalize(self, metadata: dict | None = None):
        """Write a summary index file alongside the JSONL."""
        index = {
            "task_id": self.task_id,
            "event_count": self._event_count,
            "artifacts": self._artifacts,
            **(metadata or {}),
        }
        index_path = _run_file(self.task_id, ".meta.json")
        if index_path is None:
            return
        try:
            index_path.write_text(
                json.dumps(index, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as e:
            log.warning("Failed to write run log index: %s", e)


def load_run_events(task_id: str) -> list[dict]:
    """Load all events for a task run."""
    path = _run_file(task_id, ".jsonl")
    if path is None or not path.exists():
        return []
    events = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    except Exception as e:
        log.warning("Failed to read run log %s: %s", task_id, e)
    return events


def load_run_meta(task_id: str) -> dict | None:
    """Load the run metadata/index."""
    path = _run_file(task_id, ".meta.json")
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_runs() -> list[dict]:
    """List all runs with metadata, newest first."""
    runs = []
    for meta_path in sorted(RUNS_DIR.glob("*.meta.json"), reverse=True):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            runs.append(meta)
        except Exception:
            continue
    return runs


def get_run_artifacts(task_id: str, kind: str = "") -> list[dict]:
    """Get artifacts from a run, optionally filtered by kind.

    Returns full event data for each artifact (including widget HTML).
    """
    events = load_run_events(task_id)
    if kind == "widget":
        return [e for e in events if e.get("type") == "widget_render"]
    # Return all artifact-like events
    artifact_types = {"widget_render", "tool_result"}
    return [e for e in events if e.get("type") in artifact_types]
