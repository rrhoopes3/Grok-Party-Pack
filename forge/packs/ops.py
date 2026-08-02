"""Ops capability pack — DevOps, infrastructure, and system administration."""
from forge.packs import CapabilityPack, PackBudget

OPS_PACK = CapabilityPack(
    name="ops",
    description="DevOps tasks, system administration, git workflows, database queries",
    tools=["filesystem", "search", "shell", "git", "http", "database"],
    default_model="grok-4.20-0309-non-reasoning",
    fallback_models=["gpt-5.6-luna", "claude-haiku-4-5-20251001"],
    guardrail_profile="strict",
    budget=PackBudget(max_cost_usd=3.0, max_steps=10, max_iterations_per_step=15),
    ui_panels=["output", "terminal"],
    env_required=[],
    env_optional=[],
    deps_required=[],
)
