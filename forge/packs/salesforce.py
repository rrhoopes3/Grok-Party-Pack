"""Salesforce capability pack — routes through @salesforce/mcp.

Pairs the MCP-routed Salesforce surface with email (ARC-Relay) and
filesystem so an agent can go from "read pipeline" → "draft follow-ups"
→ "log activity" in one run — all through the config-driven router.

Adding / enabling / disabling = one flip in `forge.config.MCP_SERVERS`,
or the `FORGE_MCP_SERVER_SALESFORCE_ENABLED` env var. No per-pack tool
file needed.

Tool access paths (for agent discoverability):
  - salesforce_mcp_call(tool_name, args_json)   — hard-coded namespace
  - salesforce_mcp_list_tools()                 — enumerate server tools
  - mcp_call_tool('salesforce', tool_name, …)   — generic dispatch

The legacy `sf` CLI wrappers (salesforce_soql, salesforce_describe, …)
are still registered but live under the `salesforce_cli` category, not
in this pack's default tool list. Swap the pack's `tools=["salesforce"]`
to `["salesforce_cli"]` to pin the old path if needed.
"""
from forge.packs import CapabilityPack, PackBudget


SALESFORCE_PACK = CapabilityPack(
    name="salesforce",
    description=(
        "Personal Salesforce productivity routed through @salesforce/mcp: "
        "SOQL, account briefings, pipeline hygiene, and email follow-up via "
        "ARC-Relay. Enable with FORGE_MCP_SERVER_SALESFORCE_ENABLED=true "
        "(default on) and `uv` on PATH so `uvx @salesforce/mcp` can spawn."
    ),
    tools=["salesforce", "email", "filesystem", "search"],
    # Local-first default keeps SF data off third-party servers.
    default_model="lm-studio/qwen2.5-coder-32b-instruct",
    fallback_models=["claude-sonnet-4-6", "gpt-4o-mini"],
    guardrail_profile="strict",
    budget=PackBudget(max_cost_usd=0.50, max_steps=8, max_iterations_per_step=10),
    ui_panels=["output"],
    env_required=[],
    env_optional=[
        "FORGE_MCP_SERVER_SALESFORCE_ENABLED",  # default "true"
        "FORGE_SF_ORG_ALIAS",                    # optional org pin
        "FORGE_SF_ALLOW_WRITES",                 # legacy sf CLI write gate
        "FORGE_ARCRELAY_API_KEY",                # email follow-up path
    ],
    deps_required=["mcp"],
    feature_flag="SALESFORCE_PACK_ENABLED",
)
