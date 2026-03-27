"""
Task Scheduler — cron/daemon mode for recurring Forge tasks.

Runs tasks on configurable intervals (cron expressions or simple intervals).
Each scheduled job persists across restarts via a JSON file.

Usage (programmatic):
    from forge.scheduler import Scheduler

    sched = Scheduler()
    sched.add("monitor-spy", "Check SPY price and alert if PCR > 1.5", interval_minutes=30)
    sched.add("daily-report", "Generate daily trading summary", cron="0 9 * * *")
    sched.start()  # background thread

Usage (API):
    POST /api/scheduler/jobs     — create a scheduled job
    GET  /api/scheduler/jobs     — list all jobs
    DELETE /api/scheduler/jobs/<id>  — remove a job
    POST /api/scheduler/jobs/<id>/trigger  — run a job immediately
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

log = logging.getLogger("forge.scheduler")


# ── Cron Parser (minimal) ────────────────────────────────────────────────

def _match_cron_field(field_val: str, current: int, max_val: int) -> bool:
    """Check if a cron field matches the current value."""
    if field_val == "*":
        return True
    if field_val.startswith("*/"):
        step = int(field_val[2:])
        return current % step == 0
    # Comma-separated values
    for part in field_val.split(","):
        if "-" in part:
            lo, hi = part.split("-", 1)
            if int(lo) <= current <= int(hi):
                return True
        elif int(part) == current:
            return True
    return False


def cron_matches(cron_expr: str, dt: datetime) -> bool:
    """Check if a datetime matches a cron expression (minute hour day month weekday)."""
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return False
    minute, hour, day, month, weekday = parts
    return (
        _match_cron_field(minute, dt.minute, 59)
        and _match_cron_field(hour, dt.hour, 23)
        and _match_cron_field(day, dt.day, 31)
        and _match_cron_field(month, dt.month, 12)
        and _match_cron_field(weekday, dt.weekday(), 6)  # 0=Monday
    )


# ── Data Structures ──────────────────────────────────────────────────────

@dataclass
class ScheduledJob:
    """A scheduled task definition."""
    id: str
    name: str
    task: str                          # the Forge task to execute
    interval_minutes: int = 0          # simple interval (0 = use cron)
    cron: str = ""                     # cron expression (empty = use interval)
    enabled: bool = True
    pack: str = ""                     # capability pack to use
    executor_model: str = ""
    sandbox_path: str = ""
    direct_mode: bool = True           # default to direct for scheduled tasks
    created_at: str = ""
    last_run_at: str = ""
    last_result: str = ""              # "ok" | "error" | ""
    run_count: int = 0
    error_count: int = 0
    max_failures: int = 5              # disable after N consecutive failures
    consecutive_failures: int = 0

    def is_due(self, now: datetime) -> bool:
        """Check if this job should run now."""
        if not self.enabled:
            return False

        if self.cron:
            return cron_matches(self.cron, now)

        if self.interval_minutes > 0:
            if not self.last_run_at:
                return True
            last = datetime.fromisoformat(self.last_run_at)
            return (now - last) >= timedelta(minutes=self.interval_minutes)

        return False


@dataclass
class JobRun:
    """Record of a single job execution."""
    job_id: str
    started_at: str
    finished_at: str = ""
    status: str = "running"  # running | ok | error
    summary: str = ""
    cost_usd: float = 0.0


# ── Scheduler ────────────────────────────────────────────────────────────

class Scheduler:
    """Background scheduler that runs Forge tasks on intervals or cron schedules."""

    def __init__(self, data_dir: Path | None = None):
        if data_dir is None:
            from forge.config import DATA_DIR
            data_dir = DATA_DIR
        self._data_dir = data_dir
        self._jobs_file = data_dir / "scheduler_jobs.json"
        self._runs_file = data_dir / "scheduler_runs.json"
        self._jobs: dict[str, ScheduledJob] = {}
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._task_callback: Callable | None = None
        self._load()

    def _load(self) -> None:
        """Load jobs from disk."""
        if self._jobs_file.exists():
            try:
                with open(self._jobs_file, "r") as f:
                    data = json.load(f)
                for item in data:
                    job = ScheduledJob(**item)
                    self._jobs[job.id] = job
                log.info("Loaded %d scheduled jobs", len(self._jobs))
            except Exception as e:
                log.warning("Failed to load scheduler jobs: %s", e)

    def _save(self) -> None:
        """Persist jobs to disk."""
        with self._lock:
            data = [asdict(job) for job in self._jobs.values()]
        with open(self._jobs_file, "w") as f:
            json.dump(data, f, indent=2)

    def _save_run(self, run: JobRun) -> None:
        """Append a run record."""
        runs = []
        if self._runs_file.exists():
            try:
                with open(self._runs_file, "r") as f:
                    runs = json.load(f)
            except Exception:
                pass
        runs.append(asdict(run))
        # Keep last 500 runs
        runs = runs[-500:]
        with open(self._runs_file, "w") as f:
            json.dump(runs, f, indent=2)

    def set_task_callback(self, callback: Callable) -> None:
        """Set the callback that actually runs a Forge task.

        Signature: callback(task: str, **kwargs) -> dict with 'summary', 'cost_usd', 'error'
        """
        self._task_callback = callback

    def add(
        self,
        name: str,
        task: str,
        interval_minutes: int = 0,
        cron: str = "",
        pack: str = "",
        executor_model: str = "",
        sandbox_path: str = "",
        direct_mode: bool = True,
        max_failures: int = 5,
    ) -> ScheduledJob:
        """Add a new scheduled job."""
        if not interval_minutes and not cron:
            raise ValueError("Must specify interval_minutes or cron expression")

        job = ScheduledJob(
            id=uuid.uuid4().hex[:12],
            name=name,
            task=task,
            interval_minutes=interval_minutes,
            cron=cron,
            pack=pack,
            executor_model=executor_model,
            sandbox_path=sandbox_path,
            direct_mode=direct_mode,
            created_at=datetime.now().isoformat(),
            max_failures=max_failures,
        )
        with self._lock:
            self._jobs[job.id] = job
        self._save()
        log.info("Added scheduled job: %s (%s)", job.name, job.id)
        return job

    def remove(self, job_id: str) -> bool:
        """Remove a scheduled job."""
        with self._lock:
            if job_id in self._jobs:
                del self._jobs[job_id]
                self._save()
                return True
        return False

    def get(self, job_id: str) -> ScheduledJob | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[ScheduledJob]:
        return list(self._jobs.values())

    def get_runs(self, job_id: str = "", limit: int = 20) -> list[dict]:
        """Get recent run records, optionally filtered by job_id."""
        if not self._runs_file.exists():
            return []
        try:
            with open(self._runs_file, "r") as f:
                runs = json.load(f)
            if job_id:
                runs = [r for r in runs if r.get("job_id") == job_id]
            return runs[-limit:]
        except Exception:
            return []

    def trigger(self, job_id: str) -> dict:
        """Manually trigger a job immediately."""
        job = self._jobs.get(job_id)
        if not job:
            return {"error": f"Job not found: {job_id}"}
        return self._run_job(job)

    def _run_job(self, job: ScheduledJob) -> dict:
        """Execute a single job."""
        now = datetime.now()
        run = JobRun(job_id=job.id, started_at=now.isoformat())

        log.info("Running scheduled job: %s (%s)", job.name, job.id)

        try:
            if self._task_callback is None:
                raise RuntimeError("No task callback configured — call scheduler.set_task_callback()")

            result = self._task_callback(
                job.task,
                pack=job.pack,
                executor_model=job.executor_model,
                sandbox_path=job.sandbox_path,
                direct_mode=job.direct_mode,
            )

            run.status = "ok" if not result.get("error") else "error"
            run.summary = result.get("summary", "")[:500]
            run.cost_usd = result.get("cost_usd", 0.0)

            job.last_run_at = now.isoformat()
            job.last_result = run.status
            job.run_count += 1

            if run.status == "error":
                job.error_count += 1
                job.consecutive_failures += 1
                if job.consecutive_failures >= job.max_failures:
                    job.enabled = False
                    log.warning(
                        "Job %s disabled after %d consecutive failures",
                        job.name, job.consecutive_failures,
                    )
            else:
                job.consecutive_failures = 0

        except Exception as e:
            log.exception("Scheduled job %s failed", job.name)
            run.status = "error"
            run.summary = f"{type(e).__name__}: {e}"
            job.last_result = "error"
            job.error_count += 1
            job.consecutive_failures += 1

        run.finished_at = datetime.now().isoformat()
        self._save_run(run)
        self._save()

        return {"status": run.status, "summary": run.summary}

    def _loop(self) -> None:
        """Main scheduler loop — runs in a background thread."""
        log.info("Scheduler loop started")
        while not self._stop_event.is_set():
            now = datetime.now()
            due_jobs = []
            with self._lock:
                for job in self._jobs.values():
                    if job.is_due(now):
                        due_jobs.append(job)

            for job in due_jobs:
                try:
                    self._run_job(job)
                except Exception:
                    log.exception("Unhandled error running job %s", job.id)

            # Sleep 30 seconds between checks
            self._stop_event.wait(30)

        log.info("Scheduler loop stopped")

    def start(self) -> None:
        """Start the background scheduler thread."""
        if self._thread and self._thread.is_alive():
            log.warning("Scheduler already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="forge-scheduler")
        self._thread.start()
        log.info("Scheduler started (%d jobs)", len(self._jobs))

    def stop(self) -> None:
        """Stop the scheduler."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        log.info("Scheduler stopped")

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


# ── Flask Blueprint ──────────────────────────────────────────────────────

def create_blueprint(scheduler: Scheduler):
    """Create Flask blueprint for scheduler API endpoints."""
    from flask import Blueprint, request, jsonify

    bp = Blueprint("scheduler", __name__, url_prefix="/api/scheduler")

    @bp.route("/jobs", methods=["GET"])
    def list_jobs():
        jobs = scheduler.list_jobs()
        return jsonify({
            "status": "ok",
            "running": scheduler.running,
            "jobs": [asdict(j) for j in jobs],
        })

    @bp.route("/jobs", methods=["POST"])
    def create_job():
        data = request.get_json()
        name = data.get("name", "").strip()
        task = data.get("task", "").strip()
        if not name or not task:
            return jsonify({"error": "name and task are required"}), 400

        try:
            job = scheduler.add(
                name=name,
                task=task,
                interval_minutes=data.get("interval_minutes", 0),
                cron=data.get("cron", ""),
                pack=data.get("pack", ""),
                executor_model=data.get("executor_model", ""),
                sandbox_path=data.get("sandbox_path", ""),
                direct_mode=data.get("direct_mode", True),
                max_failures=data.get("max_failures", 5),
            )
            return jsonify({"status": "ok", "job": asdict(job)}), 201
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @bp.route("/jobs/<job_id>", methods=["DELETE"])
    def delete_job(job_id):
        if scheduler.remove(job_id):
            return jsonify({"status": "ok"})
        return jsonify({"error": "Job not found"}), 404

    @bp.route("/jobs/<job_id>/trigger", methods=["POST"])
    def trigger_job(job_id):
        result = scheduler.trigger(job_id)
        if "error" in result:
            return jsonify(result), 404
        return jsonify(result)

    @bp.route("/jobs/<job_id>/runs", methods=["GET"])
    def get_runs(job_id):
        limit = request.args.get("limit", 20, type=int)
        runs = scheduler.get_runs(job_id, limit)
        return jsonify({"status": "ok", "runs": runs})

    return bp
