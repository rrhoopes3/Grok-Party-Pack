"""
Fleet Mode — multi-LLM parallel orchestration (Forge interim path).

Implements the P0/P1 concepts from ``docs/grok-build-fleet-spec.md`` and the
§10 weekend path: spawn stock ``grok-build --headless`` sessions as executors
while The Forge owns registry, routing, DAG scheduling, failover, and the
cost ledger.

Quick start::

    from forge.fleet import FleetOrchestrator, FleetStep, FleetConfig

    orch = FleetOrchestrator(config=FleetConfig(budget=2.50, max_parallel=3))
    result = orch.run([
        FleetStep(id="a", task="lint", task_class="mechanical", model="gpt-5.4-mini"),
        FleetStep(id="b", task="design", task_class="plan", model="auto"),
        FleetStep(id="c", task="wire", depends_on=["a", "b"], task_class="implement"),
    ])
"""
from forge.fleet.executor import GrokBuildExecutor, GrokBuildResult
from forge.fleet.health import (
    clear_provider_health,
    extract_text,
    health_snapshot,
    is_provider_healthy,
    mark_provider_unhealthy,
)
from forge.fleet.ledger import BudgetExceeded, CostLedger
from forge.fleet.orchestrator import (
    FleetOrchestrator,
    normalize_scope_path,
    plan_from_fleet_steps,
    plan_from_linear,
    scopes_conflict,
    steps_to_dag,
    validate_steps,
)
from forge.fleet.registry import DEFAULT_ROUTING, ModelRegistry, normalize_provider
from forge.fleet.router import AutoRouter, NoHealthyProvider, RouteDecision
from forge.fleet.types import (
    MAX_PARALLEL_CAP,
    MAX_RETRIES_CAP,
    MAX_STEPS_CAP,
    FleetConfig,
    FleetResult,
    FleetStep,
    ModelEntry,
    RerouteEvent,
    RoutingTable,
    StepResult,
)

__all__ = [
    # types
    "ModelEntry",
    "RoutingTable",
    "FleetStep",
    "FleetConfig",
    "FleetResult",
    "StepResult",
    "RerouteEvent",
    "MAX_PARALLEL_CAP",
    "MAX_RETRIES_CAP",
    "MAX_STEPS_CAP",
    # registry
    "ModelRegistry",
    "DEFAULT_ROUTING",
    "normalize_provider",
    # ledger
    "CostLedger",
    "BudgetExceeded",
    # router
    "AutoRouter",
    "RouteDecision",
    "NoHealthyProvider",
    # health
    "extract_text",
    "is_provider_healthy",
    "health_snapshot",
    "mark_provider_unhealthy",
    "clear_provider_health",
    # executor
    "GrokBuildExecutor",
    "GrokBuildResult",
    # orchestrator
    "FleetOrchestrator",
    "steps_to_dag",
    "plan_from_fleet_steps",
    "plan_from_linear",
    "scopes_conflict",
    "normalize_scope_path",
    "validate_steps",
]
