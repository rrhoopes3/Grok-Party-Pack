"""Research capability pack — deep investigation and analysis."""
from forge.packs import CapabilityPack, PackBudget

RESEARCH_PACK = CapabilityPack(
    name="research",
    description="Deep investigation, analysis, and structured summarization",
    tools=["filesystem", "search", "http", "python", "clipboard"],
    default_model="grok-4.20-0309-reasoning",
    fallback_models=["claude-sonnet-5", "gpt-5.6-terra"],
    guardrail_profile="standard",
    budget=PackBudget(max_cost_usd=2.0, max_steps=10, max_iterations_per_step=15),
    ui_panels=["output", "memory"],
    env_required=[],  # any provider key works
    env_optional=[],
    deps_required=[],
)
