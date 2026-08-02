"""Builder capability pack — coding, creating, and building software."""
from forge.packs import CapabilityPack, PackBudget

BUILDER_PACK = CapabilityPack(
    name="builder",
    description="Write code, build applications, create and modify files",
    tools=["filesystem", "search", "shell", "python", "git", "http"],
    default_model="grok-4.20-0309-reasoning",
    fallback_models=["claude-sonnet-5", "gpt-5.6-terra"],
    guardrail_profile="standard",
    budget=PackBudget(max_cost_usd=5.0, max_steps=15, max_iterations_per_step=15),
    ui_panels=["output", "files", "terminal"],
    env_required=[],
    env_optional=[],
    deps_required=[],
)
