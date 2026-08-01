"""Shared dataclasses for Fleet Mode (P0/P1 orchestration)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

TaskClass = Literal["plan", "implement", "mechanical", "verify"]
StepStatus = Literal["pending", "running", "completed", "failed", "skipped"]
TASK_CLASSES = frozenset({"plan", "implement", "mechanical", "verify"})

# Hard caps (resource / DoS bounds for untrusted plans)
MAX_PARALLEL_CAP = 16
MAX_RETRIES_CAP = 5
MAX_STEPS_CAP = 100

# Budget policy: abort | signal. "downgrade" (switch to cheaper models) is
# intentionally not implemented in P0/P1 — reserved for a future policy.
BudgetPolicy = Literal["abort", "signal"]


def _clamp_int(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


@dataclass
class ModelEntry:
    """One priced model in the fleet registry (spec §5)."""

    id: str
    provider: str
    label: str = ""
    cost_in: float = 0.0  # $/Mtok
    cost_out: float = 0.0  # $/Mtok
    tier: str = "fast"  # frontier | fast | local | auto
    base_url: str | None = None
    supports_tools: bool = True
    # Optional capability flags (spec §5); unused by P0/P1 router but carried
    # so project overrides can declare them for future parameter gating.
    supports_temperature: bool | None = None
    supports_reasoning: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "provider": self.provider,
            "label": self.label or self.id,
            "cost_in": self.cost_in,
            "cost_out": self.cost_out,
            "tier": self.tier,
            "supports_tools": self.supports_tools,
        }
        if self.base_url:
            d["base_url"] = self.base_url
        if self.supports_temperature is not None:
            d["supports_temperature"] = self.supports_temperature
        if self.supports_reasoning is not None:
            d["supports_reasoning"] = self.supports_reasoning
        return d


@dataclass
class RoutingTable:
    """``auto`` routing by task class + provider-level fallbacks (spec §5)."""

    plan: str = "grok-4.5"
    implement: str = "claude-sonnet-5"
    mechanical: str = "gpt-5.4-mini"
    verify: str = "grok-4.5"
    # provider_key (normalized lowercase) → ordered list of provider fallbacks
    fallbacks: dict[str, list[str]] = field(default_factory=dict)

    def model_for(self, task_class: str) -> str:
        key = (task_class or "implement").lower().strip()
        if key not in TASK_CLASSES:
            key = "implement"
        return getattr(self, key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan,
            "implement": self.implement,
            "mechanical": self.mechanical,
            "verify": self.verify,
            "fallbacks": dict(self.fallbacks),
        }


@dataclass
class FleetStep:
    """One schedulable unit in a fleet plan (spec §6)."""

    id: str
    task: str
    depends_on: list[str] = field(default_factory=list)
    task_class: TaskClass | str = "implement"
    model: str | None = None  # explicit override; "auto" or None → router
    persona_default: str | None = None
    file_scopes: list[str] | None = None  # None/empty → conservative serialize
    max_retries: int | None = None  # None → fleet default; always capped
    # Optional cost hint for budget reservation (USD). Under an active budget
    # this may *raise* the reserve above the tier floor but cannot undercut it
    # (under-estimates are ignored for reservation). Without a budget it is an
    # advisory upper bound only.
    estimated_cost_usd: float | None = None


@dataclass
class FleetConfig:
    """Runtime knobs for a fleet run.

    ``on_budget_exceeded``:
      - ``abort``: stop dispatching; cancel in-flight; mark remaining skipped
      - ``signal``: same stop of *new* work, but in-flight finish; no raise
      - downgrade-to-cheaper-models is **not** implemented (future)

    ``reset_ledger``: clear ledger at the start of each ``run()`` (default True).
    Set False only when intentionally aggregating cost across multiple runs.

    ``serialize_on_budget``: when a budget is set, force max_parallel=1 so
    reservation cannot be undercut by concurrent expensive steps (default True).
    """

    max_parallel: int = 4
    budget: float | None = None  # USD hard cap; None = unlimited
    max_retries: int = 1  # same-model retries before fallback chain
    models_path: str | None = None  # optional fleet.toml / models.toml / .json
    grok_build_command: str | list[str] = "grok-build"
    timeout_per_step: float = 300.0
    working_dir: str | None = None
    on_budget_exceeded: BudgetPolicy = "abort"
    # Floor cost charged when budget is set but usage JSON is missing (USD)
    missing_usage_floor_usd: float = 0.01
    # Unknown unregistered model floor under budget (USD per call)
    unknown_model_floor_usd: float = 0.05
    # Clear ledger at start of each run() so sequential runs don't stack
    reset_ledger: bool = True
    # When budget is set, dispatch at most one step at a time
    serialize_on_budget: bool = True

    def __post_init__(self) -> None:
        self.max_parallel = _clamp_int(self.max_parallel, 1, MAX_PARALLEL_CAP)
        self.max_retries = _clamp_int(self.max_retries, 0, MAX_RETRIES_CAP)
        if self.timeout_per_step < 0:
            self.timeout_per_step = 0.0
        if self.missing_usage_floor_usd < 0:
            self.missing_usage_floor_usd = 0.0
        if self.unknown_model_floor_usd < 0:
            self.unknown_model_floor_usd = 0.0


@dataclass
class RerouteEvent:
    """Logged when a step is retried or moved to a fallback model.

    ``event_type``:
      - ``retry``: same model, bounded re-attempt
      - ``fallback``: different model after failure / unhealthy
      - ``health``: pre-dispatch health reroute (never silent)
    """

    step_id: str
    from_model: str
    to_model: str
    reason: str
    attempt: int = 0
    event_type: str = "fallback"  # retry | fallback | health

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "from_model": self.from_model,
            "to_model": self.to_model,
            "reason": self.reason,
            "attempt": self.attempt,
            "event_type": self.event_type,
        }


@dataclass
class StepResult:
    """Outcome of one fleet step after retries/failover."""

    step_id: str
    status: StepStatus = "pending"
    model: str = ""
    models_tried: list[str] = field(default_factory=list)
    exit_code: int | None = None
    output: str = ""
    error: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    reroutes: list[dict[str, Any]] = field(default_factory=list)
    duration_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "status": self.status,
            "model": self.model,
            "models_tried": list(self.models_tried),
            "exit_code": self.exit_code,
            "output": self.output,
            "error": self.error,
            "cost_usd": self.cost_usd,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reroutes": list(self.reroutes),
            "duration_s": self.duration_s,
        }


@dataclass
class FleetResult:
    """Structured result of a full fleet orchestration run."""

    success: bool
    steps: dict[str, StepResult] = field(default_factory=dict)
    ledger: dict[str, Any] = field(default_factory=dict)
    reroute_log: list[dict[str, Any]] = field(default_factory=list)
    budget_exceeded: bool = False
    error: str = ""
    plan_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "steps": {k: v.to_dict() for k, v in self.steps.items()},
            "ledger": self.ledger,
            "reroute_log": list(self.reroute_log),
            "budget_exceeded": self.budget_exceeded,
            "error": self.error,
            "plan_id": self.plan_id,
        }
