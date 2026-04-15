"""Arena capability pack — gladiatorial combat and collaboration between agents."""
from forge.packs import CapabilityPack, PackBudget

ARENA_PACK = CapabilityPack(
    name="arena",
    description="Agent vs agent combat, collaboration scenarios, judged by the Pantheon",
    tools=["filesystem", "search", "shell", "python", "git", "browser", "generative_ui"],
    default_model="grok-4.20-0309-reasoning",
    fallback_models=["claude-sonnet-4-20250514", "gpt-4o"],
    guardrail_profile="permissive",
    budget=PackBudget(max_cost_usd=10.0, max_steps=20, max_iterations_per_step=15),
    ui_panels=["output", "arena_scoreboard", "widgets"],
    env_required=["XAI_API_KEY"],  # Pantheon judging needs multi-agent API
    env_optional=["ANTHROPIC_API_KEY", "OPENAI_API_KEY"],
    deps_required=[],
)
