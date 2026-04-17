"""Tests for config-driven MCP router dispatch."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from forge import mcp_client
from forge.mcp_client import (
    MCPRouter, reset_router, get_router, route_call_tool, route_list_tools,
    set_default_agent_id,
)


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_everything(tmp_path, monkeypatch):
    """Route vault/graph to temp dirs and reset router singleton."""
    monkeypatch.setattr("forge.config.VAULTS_DIR", tmp_path / "vaults")
    monkeypatch.setattr("forge.config.DATA_DIR", tmp_path)
    (tmp_path / "vaults").mkdir(exist_ok=True)
    mcp_client._vault_cache.clear()
    mcp_client._graph_cache.clear()
    old_id = mcp_client._DEFAULT_AGENT_ID
    set_default_agent_id(f"test-router-{tmp_path.name}")
    reset_router(None)
    yield
    reset_router(None)
    set_default_agent_id(old_id)


@pytest.fixture
def router():
    """Router with a test-friendly config: blender enabled, disabled_server disabled, no external spawn."""
    return MCPRouter({
        "blender": {
            "command": ["uvx", "blender-mcp"],
            "enabled": True,
            "auto_start": True,
            "timeout": 30.0,
        },
        "disabled_server": {
            "command": ["uvx", "whatever"],
            "enabled": False,
            "auto_start": False,
            "timeout": 10.0,
        },
        "no_command_server": {
            "command": [],
            "enabled": True,
            "auto_start": False,
            "timeout": 10.0,
        },
    })


# ── Inspection ──────────────────────────────────────────────────────────


def test_active_namespaces_includes_internal_and_enabled_external(router):
    active = router.active_namespaces()
    assert "forge:vault" in active
    assert "forge:graph" in active
    assert "blender" in active
    assert "disabled_server" not in active


def test_configured_servers_returns_full_map(router):
    cfg = router.configured_servers()
    assert set(cfg.keys()) == {"blender", "disabled_server", "no_command_server"}
    assert cfg["blender"]["enabled"] is True
    assert cfg["disabled_server"]["enabled"] is False


def test_summary_banner_lists_internal_and_active(router):
    banner = router.summary_banner()
    assert "forge:vault" in banner
    assert "forge:graph" in banner
    assert "blender" in banner


# ── Internal dispatch (regression: no path change from last PR) ─────────


def test_internal_vault_store_via_router(router):
    out = router.call_tool("forge:vault", "store",
                           {"key": "acme_deal", "value": "stage 3 requested"})
    parsed = json.loads(out)
    assert parsed["ok"] is True
    assert parsed["namespace"] == "forge:vault"


def test_internal_vault_recall_via_router(router):
    router.call_tool("forge:vault", "store",
                     {"key": "deal_stage", "value": "client requested rollout extension"})
    out = router.call_tool("forge:vault", "recall",
                           {"query": "rollout extension", "limit": 5})
    parsed = json.loads(out)
    assert parsed["namespace"] == "forge:vault"
    topics = [m["topic"] for m in parsed["matches"]]
    assert "deal_stage" in topics


def test_internal_graph_store_via_router(router):
    out = router.call_tool("forge:graph", "store",
                           {"key": "company:acme", "value": '{"kind":"company","label":"Acme"}'})
    parsed = json.loads(out)
    assert parsed["ok"] is True
    assert parsed["kind"] == "company"


def test_internal_store_requires_key(router):
    out = router.call_tool("forge:vault", "store", {"value": "orphan"})
    parsed = json.loads(out)
    assert "error" in parsed
    assert "key" in parsed["error"].lower()


def test_internal_unknown_verb_errors(router):
    out = router.call_tool("forge:vault", "delete", {"key": "x"})
    parsed = json.loads(out)
    assert "error" in parsed
    assert "store" in parsed["error"] and "recall" in parsed["error"]


def test_internal_unknown_namespace_errors(router):
    out = router.call_tool("forge:unknown_ns", "store", {"key": "x", "value": "y"})
    parsed = json.loads(out)
    assert "error" in parsed
    assert "forge:unknown_ns" in parsed["error"]


# ── External dispatch ───────────────────────────────────────────────────


def test_external_bare_server_name_routes(router):
    """`blender` (no colon) should resolve to the blender server."""
    with patch("forge.mcp_client.call_mcp_tool", return_value='{"ok":true}') as mock:
        router.call_tool("blender", "get_scene_info", {})
    assert mock.called
    kwargs = mock.call_args.kwargs
    assert kwargs["command"] == "uvx"
    assert kwargs["args"] == ["blender-mcp"]
    assert kwargs["tool_name"] == "get_scene_info"


def test_external_colon_form_also_routes(router):
    """`blender:whatever` should also resolve to blender (scope match)."""
    with patch("forge.mcp_client.call_mcp_tool", return_value='{"ok":true}') as mock:
        router.call_tool("blender:x", "get_scene_info", {})
    assert mock.called


def test_external_mcp_prefix_form_routes(router):
    """`mcp:blender` prefix should also resolve."""
    with patch("forge.mcp_client.call_mcp_tool", return_value='{"ok":true}') as mock:
        router.call_tool("mcp:blender", "get_scene_info", {})
    assert mock.called


def test_external_passes_timeout_from_config(router):
    with patch("forge.mcp_client.call_mcp_tool", return_value='{"ok":true}') as mock:
        router.call_tool("blender", "get_scene_info", {})
    assert mock.call_args.kwargs["timeout"] == 30.0


def test_disabled_server_returns_friendly_error(router):
    out = router.call_tool("disabled_server", "anything", {})
    parsed = json.loads(out)
    assert "error" in parsed
    assert "disabled" in parsed["error"]
    assert "FORGE_MCP_SERVER_DISABLED_SERVER_ENABLED" in parsed["suggestion"]


def test_no_command_server_returns_friendly_error(router):
    out = router.call_tool("no_command_server", "anything", {})
    parsed = json.loads(out)
    assert "error" in parsed
    assert "No command configured" in parsed["error"]


def test_unknown_namespace_suggests_available(router):
    out = router.call_tool("notion", "page_get", {})
    parsed = json.loads(out)
    assert "error" in parsed
    assert "notion" in parsed["error"]
    assert "forge:vault" in parsed["suggestion"]
    assert "blender" in parsed["suggestion"]


# ── list_namespace_tools ────────────────────────────────────────────────


def test_list_internal_namespace_returns_store_recall(router):
    out = router.list_namespace_tools("forge:vault")
    parsed = json.loads(out)
    names = [t["name"] for t in parsed["tools"]]
    assert names == ["store", "recall"]


def test_list_disabled_server_returns_error(router):
    out = router.list_namespace_tools("disabled_server")
    parsed = json.loads(out)
    assert "error" in parsed
    assert "disabled" in parsed["error"]


# ── Singleton + reset ───────────────────────────────────────────────────


def test_get_router_returns_singleton():
    reset_router(None)
    r1 = get_router()
    r2 = get_router()
    assert r1 is r2


def test_reset_router_clears():
    r1 = get_router()
    reset_router(None)
    r2 = get_router()
    assert r1 is not r2


# ── Tool registration ──────────────────────────────────────────────────


def test_mcp_call_tool_registered():
    from forge.tools import create_registry
    reg = create_registry()
    names = set(reg.list_tools())
    assert "mcp_call_tool" in names
    assert "mcp_list_tools" in names
    assert "mcp_list_namespaces" in names


def test_mcp_call_tool_rejects_non_json_args():
    from forge.tools.mcp import mcp_call_tool
    out = mcp_call_tool("forge:vault", "store", "{not json}")
    parsed = json.loads(out)
    assert "error" in parsed
    assert "valid JSON" in parsed["error"]


def test_mcp_call_tool_rejects_non_object_args():
    from forge.tools.mcp import mcp_call_tool
    out = mcp_call_tool("forge:vault", "store", "[1,2,3]")
    parsed = json.loads(out)
    assert "error" in parsed
    assert "JSON object" in parsed["error"]


def test_mcp_call_tool_happy_path_via_tool_facade():
    from forge.tools.mcp import mcp_call_tool
    out = mcp_call_tool("forge:vault", "store",
                        json.dumps({"key": "tool_facade_test", "value": "ok"}))
    parsed = json.loads(out)
    assert parsed["ok"] is True


def test_mcp_list_namespaces_tool():
    from forge.tools.mcp import mcp_list_namespaces
    out = mcp_list_namespaces()
    parsed = json.loads(out)
    assert "active" in parsed
    assert "configured_external" in parsed
    assert "forge:vault" in parsed["active"]


# ── Config contract ────────────────────────────────────────────────────


def test_config_has_mcp_servers_dict():
    from forge.config import MCP_SERVERS
    assert isinstance(MCP_SERVERS, dict)
    assert "blender" in MCP_SERVERS
    for name, cfg in MCP_SERVERS.items():
        assert "command" in cfg
        assert "enabled" in cfg
        assert "auto_start" in cfg
