"""Tests for the Capability Packs system."""
import os
import pytest
from unittest.mock import patch

from forge.packs import (
    CapabilityPack, PackBudget, PackRegistry, ReadinessReport, ReadinessCheck,
    get_registry,
)
from forge.packs.research import RESEARCH_PACK
from forge.packs.builder import BUILDER_PACK
from forge.packs.ops import OPS_PACK
from forge.packs.trading import TRADING_PACK
from forge.packs.arena import ARENA_PACK
from forge.packs.email import EMAIL_PACK
from forge.packs.salesforce import SALESFORCE_PACK
from forge.packs.blender import BLENDER_PACK


# ── CapabilityPack Tests ─────────────────────────────────────────────────


class TestCapabilityPack:
    def test_basic_construction(self):
        pack = CapabilityPack(
            name="test",
            description="Test pack",
            tools=["filesystem", "search"],
        )
        assert pack.name == "test"
        assert pack.description == "Test pack"
        assert pack.tools == ["filesystem", "search"]
        assert pack.guardrail_profile == "standard"
        assert pack.budget.max_cost_usd == 5.0

    def test_custom_budget(self):
        pack = CapabilityPack(
            name="tight",
            description="Tight budget",
            tools=["http"],
            budget=PackBudget(max_cost_usd=0.50, max_steps=3),
        )
        assert pack.budget.max_cost_usd == 0.50
        assert pack.budget.max_steps == 3

    def test_to_dict_has_readiness(self):
        pack = CapabilityPack(
            name="test",
            description="Test",
            tools=["filesystem"],
        )
        d = pack.to_dict()
        assert "readiness" in d
        assert d["readiness"]["pack"] == "test"
        assert d["readiness"]["state"] in ("ready", "degraded", "unavailable")

    def test_to_dict_fields(self):
        pack = CapabilityPack(
            name="test",
            description="Test pack",
            tools=["filesystem"],
            default_model="grok-4-1-fast-reasoning",
            guardrail_profile="strict",
        )
        d = pack.to_dict()
        assert d["name"] == "test"
        assert d["description"] == "Test pack"
        assert d["tools"] == ["filesystem"]
        assert d["default_model"] == "grok-4-1-fast-reasoning"
        assert d["guardrail_profile"] == "strict"
        assert "budget" in d
        assert d["budget"]["max_cost_usd"] == 5.0


# ── Readiness Tests ──────────────────────────────────────────────────────


class TestReadiness:
    def test_all_ready(self):
        """Pack with no requirements should be ready (if a provider key exists)."""
        pack = CapabilityPack(name="test", description="t", tools=[])
        with patch.dict(os.environ, {"XAI_API_KEY": "test-key"}):
            report = pack.check_readiness()
        assert report.state == "ready" or report.state == "degraded"

    def test_missing_required_env(self):
        """Missing required env var → unavailable."""
        pack = CapabilityPack(
            name="test", description="t", tools=[],
            env_required=["FORGE_NONEXISTENT_KEY_12345"],
        )
        with patch.dict(os.environ, {}, clear=False):
            # Ensure the key doesn't exist
            os.environ.pop("FORGE_NONEXISTENT_KEY_12345", None)
            report = pack.check_readiness()
        assert report.state == "unavailable"
        assert any(c.status == "unavailable" and "FORGE_NONEXISTENT_KEY_12345" in c.name
                    for c in report.checks)

    def test_missing_optional_env_is_degraded(self):
        """Missing optional env var → degraded, not unavailable."""
        pack = CapabilityPack(
            name="test", description="t", tools=[],
            env_optional=["FORGE_OPTIONAL_THING_99999"],
        )
        with patch.dict(os.environ, {"XAI_API_KEY": "test-key"}, clear=False):
            os.environ.pop("FORGE_OPTIONAL_THING_99999", None)
            report = pack.check_readiness()
        # Should be degraded (optional missing) but not unavailable
        assert report.state in ("ready", "degraded")
        optional_checks = [c for c in report.checks if c.name == "FORGE_OPTIONAL_THING_99999"]
        assert len(optional_checks) == 1
        assert optional_checks[0].status == "degraded"

    def test_missing_dep_is_unavailable(self):
        """Missing Python dependency → unavailable."""
        pack = CapabilityPack(
            name="test", description="t", tools=[],
            deps_required=["nonexistent_package_xyz_99"],
        )
        report = pack.check_readiness()
        assert any(c.status == "unavailable" and "nonexistent_package_xyz_99" in c.name
                    for c in report.checks)

    def test_present_dep_is_ready(self):
        """Installed Python dependency → ready."""
        pack = CapabilityPack(
            name="test", description="t", tools=[],
            deps_required=["json"],  # stdlib, always available
        )
        report = pack.check_readiness()
        dep_checks = [c for c in report.checks if c.name == "json"]
        assert len(dep_checks) == 1
        assert dep_checks[0].status == "ready"

    def test_no_provider_key_is_unavailable(self):
        """No provider API key at all → unavailable."""
        pack = CapabilityPack(name="test", description="t", tools=[])
        with patch.dict(os.environ, {
            "XAI_API_KEY": "",
            "ANTHROPIC_API_KEY": "",
            "OPENAI_API_KEY": "",
        }, clear=False):
            report = pack.check_readiness()
        provider_checks = [c for c in report.checks if c.name == "provider"]
        assert len(provider_checks) == 1
        assert provider_checks[0].status == "unavailable"

    def test_readiness_report_summary(self):
        report = ReadinessReport(
            pack_name="test",
            checks=[
                ReadinessCheck(name="a", status="ready"),
                ReadinessCheck(name="b", status="degraded", message="missing optional"),
            ],
        )
        assert report.state == "degraded"
        assert "missing optional" in report.summary

    def test_readiness_report_to_dict(self):
        report = ReadinessReport(
            pack_name="test",
            checks=[ReadinessCheck(name="x", status="ready", message="ok")],
        )
        d = report.to_dict()
        assert d["pack"] == "test"
        assert d["state"] == "ready"
        assert len(d["checks"]) == 1


# ── PackRegistry Tests ───────────────────────────────────────────────────


class TestPackRegistry:
    def test_register_and_get(self):
        reg = PackRegistry()
        pack = CapabilityPack(name="test", description="t", tools=["shell"])
        reg.register(pack)
        assert reg.get("test") is pack
        assert reg.get("nonexistent") is None

    def test_list_packs(self):
        reg = PackRegistry()
        reg.register(CapabilityPack(name="a", description="a", tools=[]))
        reg.register(CapabilityPack(name="b", description="b", tools=[]))
        assert len(reg.list_packs()) == 2
        assert set(reg.list_names()) == {"a", "b"}

    def test_to_dict(self):
        reg = PackRegistry()
        reg.register(CapabilityPack(name="test", description="t", tools=[]))
        result = reg.to_dict()
        assert len(result) == 1
        assert result[0]["name"] == "test"

    def test_readiness_all(self):
        reg = PackRegistry()
        reg.register(CapabilityPack(name="a", description="a", tools=[]))
        reg.register(CapabilityPack(name="b", description="b", tools=[]))
        reports = reg.readiness_all()
        assert "a" in reports
        assert "b" in reports

    def test_duplicate_register_overwrites(self):
        reg = PackRegistry()
        reg.register(CapabilityPack(name="x", description="first", tools=[]))
        reg.register(CapabilityPack(name="x", description="second", tools=[]))
        assert reg.get("x").description == "second"
        assert len(reg.list_packs()) == 1


# ── Built-in Pack Tests ──────────────────────────────────────────────────


class TestBuiltinPacks:
    def test_all_packs_load(self):
        """All 8 built-in packs should be loadable."""
        packs = [RESEARCH_PACK, BUILDER_PACK, OPS_PACK, TRADING_PACK, ARENA_PACK, EMAIL_PACK, SALESFORCE_PACK, BLENDER_PACK]
        names = {p.name for p in packs}
        assert names == {"research", "builder", "ops", "trading", "arena", "email", "salesforce", "blender"}

    def test_all_packs_have_tools(self):
        for pack in [RESEARCH_PACK, BUILDER_PACK, OPS_PACK, TRADING_PACK, ARENA_PACK, EMAIL_PACK, SALESFORCE_PACK, BLENDER_PACK]:
            assert len(pack.tools) > 0, f"{pack.name} has no tools"

    def test_all_packs_have_description(self):
        for pack in [RESEARCH_PACK, BUILDER_PACK, OPS_PACK, TRADING_PACK, ARENA_PACK, EMAIL_PACK, SALESFORCE_PACK, BLENDER_PACK]:
            assert pack.description, f"{pack.name} has no description"

    def test_trading_pack_is_strict(self):
        assert TRADING_PACK.guardrail_profile == "strict"

    def test_arena_pack_is_permissive(self):
        assert ARENA_PACK.guardrail_profile == "permissive"

    def test_ops_pack_is_strict(self):
        assert OPS_PACK.guardrail_profile == "strict"

    def test_trading_requires_tradier_key(self):
        assert "FORGE_TRADIER_API_KEY" in TRADING_PACK.env_required

    def test_arena_requires_xai_key(self):
        assert "XAI_API_KEY" in ARENA_PACK.env_required

    def test_email_requires_arcrelay_key(self):
        assert "FORGE_ARCRELAY_API_KEY" in EMAIL_PACK.env_required

    def test_trading_has_feature_flag(self):
        assert TRADING_PACK.feature_flag == "TRADING_ENABLED"

    def test_email_has_feature_flag(self):
        assert EMAIL_PACK.feature_flag == "EMAIL_AGENT_ENABLED"

    def test_salesforce_pack_is_strict(self):
        assert SALESFORCE_PACK.guardrail_profile == "strict"

    def test_salesforce_has_feature_flag(self):
        assert SALESFORCE_PACK.feature_flag == "SALESFORCE_PACK_ENABLED"

    def test_salesforce_includes_email_tool(self):
        """Salesforce pack chains into email for ARC-Relay follow-ups."""
        assert "email" in SALESFORCE_PACK.tools
        assert "salesforce" in SALESFORCE_PACK.tools

    def test_blender_has_feature_flag(self):
        assert BLENDER_PACK.feature_flag == "BLENDER_PACK_ENABLED"

    def test_blender_requires_mcp_dep(self):
        assert "mcp" in BLENDER_PACK.deps_required

    def test_blender_defaults_to_vision_model(self):
        """Viewport screenshots need a vision-capable default model."""
        assert BLENDER_PACK.default_model  # not empty

    def test_budgets_are_reasonable(self):
        """Stricter packs should have tighter budgets."""
        assert TRADING_PACK.budget.max_cost_usd <= BUILDER_PACK.budget.max_cost_usd
        assert EMAIL_PACK.budget.max_cost_usd <= RESEARCH_PACK.budget.max_cost_usd

    def test_all_packs_serialize(self):
        """All packs should serialize to dict without error."""
        for pack in [RESEARCH_PACK, BUILDER_PACK, OPS_PACK, TRADING_PACK, ARENA_PACK, EMAIL_PACK, SALESFORCE_PACK, BLENDER_PACK]:
            d = pack.to_dict()
            assert d["name"] == pack.name
            assert "readiness" in d


# ── Global Registry Tests ────────────────────────────────────────────────


class TestGlobalRegistry:
    def test_get_registry_returns_populated(self):
        """Global registry should have all 8 built-in packs."""
        # Reset singleton for clean test
        import forge.packs as packs_mod
        packs_mod._registry = None
        reg = get_registry()
        assert len(reg.list_packs()) == 8
        assert set(reg.list_names()) == {"research", "builder", "ops", "trading", "arena", "email", "salesforce", "blender"}

    def test_get_registry_is_singleton(self):
        """Calling get_registry twice returns same instance."""
        import forge.packs as packs_mod
        packs_mod._registry = None
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2
