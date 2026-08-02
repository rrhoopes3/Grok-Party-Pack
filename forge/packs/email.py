"""Email capability pack — email management, domain admin, and auto-response."""
from forge.packs import CapabilityPack, PackBudget

EMAIL_PACK = CapabilityPack(
    name="email",
    description="Email management via ARC-Relay: domain admin, alias management, log analysis",
    tools=["email", "http", "filesystem", "python"],
    default_model="grok-4.20-0309-non-reasoning",
    fallback_models=["gpt-5.6-luna", "claude-haiku-4-5-20251001"],
    guardrail_profile="strict",
    budget=PackBudget(max_cost_usd=1.0, max_steps=5, max_iterations_per_step=10),
    ui_panels=["output", "email_logs"],
    env_required=["FORGE_ARCRELAY_API_KEY"],
    env_optional=["FORGE_ARCRELAY_WEBHOOK_SECRET"],
    deps_required=[],
    feature_flag="EMAIL_AGENT_ENABLED",
)
