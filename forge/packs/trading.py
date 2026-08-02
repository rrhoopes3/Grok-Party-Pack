"""Trading capability pack — market analysis, portfolio management, trade execution."""
from forge.packs import CapabilityPack, PackBudget

TRADING_PACK = CapabilityPack(
    name="trading",
    description="Market analysis, portfolio tracking, PCR dashboard, trade execution",
    tools=["trading", "http", "python", "filesystem"],
    default_model="grok-4.20-0309-reasoning",
    fallback_models=["gpt-5.6-terra", "claude-sonnet-5"],
    guardrail_profile="strict",
    budget=PackBudget(max_cost_usd=1.0, max_steps=5, max_iterations_per_step=10),
    ui_panels=["output", "trading_dashboard", "portfolio"],
    env_required=[],
    env_optional=[],
    deps_required=["yfinance"],
    feature_flag="TRADING_ENABLED",
)
