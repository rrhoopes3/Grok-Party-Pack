"""
Observability — Prometheus metrics, structured logging, and alerting.

Exposes a /metrics endpoint in Prometheus text exposition format.
Tracks task execution, tool usage, costs, latency, and error rates.
No external dependencies required — implements the text format directly.

Usage:
    from forge.observability import metrics, init_observability

    # In Flask app:
    init_observability(app)

    # Record metrics anywhere:
    metrics.task_started("task_123", model="grok-4.20")
    metrics.task_completed("task_123", duration=12.5, cost_usd=0.03)
    metrics.tool_called("read_file", duration=0.01)
    metrics.tool_error("http_get", error_type="timeout")
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field

log = logging.getLogger("forge.observability")


# ── Metric Types ─────────────────────────────────────────────────────────

@dataclass
class Counter:
    """Monotonically increasing counter."""
    name: str
    help: str
    labels: list[str] = field(default_factory=list)
    _values: dict = field(default_factory=lambda: defaultdict(float))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def inc(self, amount: float = 1.0, **label_values) -> None:
        key = tuple(sorted(label_values.items())) if label_values else ()
        with self._lock:
            self._values[key] += amount

    def render(self) -> str:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]
        with self._lock:
            for key, value in sorted(self._values.items()):
                labels = ""
                if key:
                    label_parts = [f'{k}="{v}"' for k, v in key]
                    labels = "{" + ",".join(label_parts) + "}"
                lines.append(f"{self.name}{labels} {value}")
        return "\n".join(lines)


@dataclass
class Gauge:
    """Value that can go up and down."""
    name: str
    help: str
    labels: list[str] = field(default_factory=list)
    _values: dict = field(default_factory=lambda: defaultdict(float))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def set(self, value: float, **label_values) -> None:
        key = tuple(sorted(label_values.items())) if label_values else ()
        with self._lock:
            self._values[key] = value

    def inc(self, amount: float = 1.0, **label_values) -> None:
        key = tuple(sorted(label_values.items())) if label_values else ()
        with self._lock:
            self._values[key] += amount

    def dec(self, amount: float = 1.0, **label_values) -> None:
        key = tuple(sorted(label_values.items())) if label_values else ()
        with self._lock:
            self._values[key] -= amount

    def render(self) -> str:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} gauge"]
        with self._lock:
            for key, value in sorted(self._values.items()):
                labels = ""
                if key:
                    label_parts = [f'{k}="{v}"' for k, v in key]
                    labels = "{" + ",".join(label_parts) + "}"
                lines.append(f"{self.name}{labels} {value}")
        return "\n".join(lines)


@dataclass
class Histogram:
    """Distribution of values with configurable buckets."""
    name: str
    help: str
    buckets: list[float] = field(default_factory=lambda: [0.01, 0.05, 0.1, 0.5, 1, 5, 10, 30, 60, 120])
    labels: list[str] = field(default_factory=list)
    _counts: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))
    _sums: dict = field(default_factory=lambda: defaultdict(float))
    _totals: dict = field(default_factory=lambda: defaultdict(int))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def observe(self, value: float, **label_values) -> None:
        key = tuple(sorted(label_values.items())) if label_values else ()
        with self._lock:
            self._sums[key] += value
            self._totals[key] += 1
            for bucket in self.buckets:
                if value <= bucket:
                    self._counts[key][bucket] += 1

    def render(self) -> str:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        with self._lock:
            for key in sorted(set(list(self._sums.keys()) + list(self._totals.keys()))):
                labels_str = ""
                if key:
                    label_parts = [f'{k}="{v}"' for k, v in key]
                    labels_str = ",".join(label_parts) + ","

                cumulative = 0
                for bucket in self.buckets:
                    cumulative += self._counts[key].get(bucket, 0)
                    lines.append(f'{self.name}_bucket{{{labels_str}le="{bucket}"}} {cumulative}')
                lines.append(f'{self.name}_bucket{{{labels_str}le="+Inf"}} {self._totals[key]}')
                lines.append(f"{self.name}_sum{{{labels_str.rstrip(',')}}} {self._sums[key]}")
                lines.append(f"{self.name}_count{{{labels_str.rstrip(',')}}} {self._totals[key]}")
        return "\n".join(lines)


# ── Metrics Registry ─────────────────────────────────────────────────────

class ForgeMetrics:
    """Central metrics registry for The Forge."""

    def __init__(self):
        # Task metrics
        self.tasks_total = Counter(
            "forge_tasks_total",
            "Total number of tasks submitted",
        )
        self.tasks_active = Gauge(
            "forge_tasks_active",
            "Number of currently running tasks",
        )
        self.task_duration = Histogram(
            "forge_task_duration_seconds",
            "Task execution duration in seconds",
            buckets=[1, 5, 10, 30, 60, 120, 300, 600],
        )
        self.task_cost = Counter(
            "forge_task_cost_usd_total",
            "Total cost of task execution in USD",
        )

        # Tool metrics
        self.tool_calls_total = Counter(
            "forge_tool_calls_total",
            "Total tool invocations",
            labels=["tool"],
        )
        self.tool_errors_total = Counter(
            "forge_tool_errors_total",
            "Total tool errors",
            labels=["tool", "error_type"],
        )
        self.tool_duration = Histogram(
            "forge_tool_duration_seconds",
            "Tool execution duration in seconds",
            labels=["tool"],
        )

        # Model metrics
        self.model_requests_total = Counter(
            "forge_model_requests_total",
            "Total LLM API requests",
            labels=["model", "provider"],
        )
        self.model_tokens_total = Counter(
            "forge_model_tokens_total",
            "Total tokens consumed",
            labels=["model", "direction"],  # direction: input/output
        )

        # Arena metrics
        self.arena_matches_total = Counter(
            "forge_arena_matches_total",
            "Total arena matches",
            labels=["mode"],  # combat/collab/swarm
        )

        # Toll metrics
        self.toll_revenue_usd = Counter(
            "forge_toll_revenue_usd_total",
            "Total toll revenue in USD",
        )
        self.toll_transactions_total = Counter(
            "forge_toll_transactions_total",
            "Total toll transactions",
        )

        # Scheduler metrics
        self.scheduler_runs_total = Counter(
            "forge_scheduler_runs_total",
            "Total scheduled job runs",
            labels=["job", "status"],
        )

        # System metrics
        self.uptime_seconds = Gauge(
            "forge_uptime_seconds",
            "Server uptime in seconds",
        )
        self._start_time = time.time()

        self._all_metrics = [
            self.tasks_total, self.tasks_active, self.task_duration, self.task_cost,
            self.tool_calls_total, self.tool_errors_total, self.tool_duration,
            self.model_requests_total, self.model_tokens_total,
            self.arena_matches_total,
            self.toll_revenue_usd, self.toll_transactions_total,
            self.scheduler_runs_total,
            self.uptime_seconds,
        ]

    # ── Convenience Methods ──────────────────────────────────────────

    def task_started(self, task_id: str, model: str = "") -> None:
        self.tasks_total.inc()
        self.tasks_active.inc()

    def task_completed(self, task_id: str, duration: float = 0, cost_usd: float = 0) -> None:
        self.tasks_active.dec()
        if duration:
            self.task_duration.observe(duration)
        if cost_usd:
            self.task_cost.inc(cost_usd)

    def task_failed(self, task_id: str, duration: float = 0) -> None:
        self.tasks_active.dec()
        if duration:
            self.task_duration.observe(duration)

    def tool_called(self, tool_name: str, duration: float = 0) -> None:
        self.tool_calls_total.inc(tool=tool_name)
        if duration:
            self.tool_duration.observe(duration, tool=tool_name)

    def tool_error(self, tool_name: str, error_type: str = "unknown") -> None:
        self.tool_errors_total.inc(tool=tool_name, error_type=error_type)

    def model_request(self, model: str, provider: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.model_requests_total.inc(model=model, provider=provider)
        if input_tokens:
            self.model_tokens_total.inc(input_tokens, model=model, direction="input")
        if output_tokens:
            self.model_tokens_total.inc(output_tokens, model=model, direction="output")

    # ── Rendering ────────────────────────────────────────────────────

    def render(self) -> str:
        """Render all metrics in Prometheus text exposition format."""
        self.uptime_seconds.set(time.time() - self._start_time)
        parts = [m.render() for m in self._all_metrics]
        return "\n\n".join(parts) + "\n"

    def snapshot(self) -> dict:
        """Return a JSON-friendly snapshot of key metrics."""
        return {
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "tasks_total": sum(self.tasks_total._values.values()),
            "tasks_active": sum(self.tasks_active._values.values()),
            "total_cost_usd": round(sum(self.task_cost._values.values()), 4),
            "tool_calls": sum(self.tool_calls_total._values.values()),
            "tool_errors": sum(self.tool_errors_total._values.values()),
            "model_requests": sum(self.model_requests_total._values.values()),
        }


# ── Singleton ────────────────────────────────────────────────────────────

metrics = ForgeMetrics()


# ── Flask Integration ────────────────────────────────────────────────────

def init_observability(app) -> None:
    """Add /metrics and /api/metrics endpoints to the Flask app."""
    from flask import Response, jsonify

    @app.route("/metrics")
    def prometheus_metrics():
        """Prometheus scrape endpoint."""
        return Response(metrics.render(), mimetype="text/plain; version=0.0.4")

    @app.route("/api/metrics")
    def api_metrics():
        """JSON metrics snapshot."""
        return jsonify(metrics.snapshot())

    log.info("Observability endpoints registered (/metrics, /api/metrics)")
