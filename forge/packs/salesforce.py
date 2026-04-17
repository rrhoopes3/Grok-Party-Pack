"""Salesforce capability pack — personal CRM productivity.

Pairs SOQL/record tools with email (ARC-Relay) so the agent can go from
"scan stale pipeline" → "draft follow-ups" → "log activity" in one run.

Governance-clean default: LM Studio provider so nothing leaves the machine.
"""
from forge.packs import CapabilityPack, PackBudget


SALESFORCE_PACK = CapabilityPack(
    name="salesforce",
    description=(
        "Personal Salesforce productivity: SOQL, account briefings, pipeline hygiene, "
        "and email follow-up via ARC-Relay. Read-only unless FORGE_SF_ALLOW_WRITES=true."
    ),
    tools=["salesforce", "email", "filesystem", "search"],
    # Local-first default keeps SF data off third-party servers.
    default_model="lm-studio/qwen2.5-coder-32b-instruct",
    fallback_models=["claude-sonnet-4-6", "gpt-4o-mini"],
    guardrail_profile="strict",
    budget=PackBudget(max_cost_usd=0.50, max_steps=8, max_iterations_per_step=10),
    ui_panels=["output"],
    env_required=[],  # sf CLI auth lives outside env — user runs `sf org login web`
    env_optional=["FORGE_SF_ORG_ALIAS", "FORGE_SF_ALLOW_WRITES", "FORGE_ARCRELAY_API_KEY"],
    deps_required=[],
    feature_flag="SALESFORCE_PACK_ENABLED",
)
