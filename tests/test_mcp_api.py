"""Tests for the MCP Hub sidebar endpoints.

/api/mcp/status      — per-server health with 5s cache + optional live ping
/api/mcp/namespaces  — full sidebar render payload (internal + external)
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forge import mcp_client
from forge.mcp_client import reset_router, set_default_agent_id


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolate_mcp(tmp_path, monkeypatch):
    """Route vault/graph to temp dirs, reset router + status cache."""
    monkeypatch.setattr("forge.config.VAULTS_DIR", tmp_path / "vaults")
    monkeypatch.setattr("forge.config.DATA_DIR", tmp_path)
    (tmp_path / "vaults").mkdir(exist_ok=True)
    mcp_client._vault_cache.clear()
    mcp_client._graph_cache.clear()
    old_id = mcp_client._DEFAULT_AGENT_ID
    set_default_agent_id(f"test-mcp-api-{tmp_path.name}")
    reset_router({
        "blender": {
            "command": ["uvx", "blender-mcp"],
            "enabled": True,
            "auto_start": True,
            "timeout": 30.0,
        },
        "salesforce": {
            "command": ["uvx", "@salesforce/mcp"],
            "enabled": True,
            "auto_start": True,
            "timeout": 60.0,
        },
        "disabled_srv": {
            "command": ["uvx", "nope"],
            "enabled": False,
            "auto_start": False,
            "timeout": 10.0,
        },
    })
    from forge import app as app_mod
    app_mod._mcp_status_cache.clear()
    yield
    reset_router(None)
    app_mod._mcp_status_cache.clear()
    set_default_agent_id(old_id)


@pytest.fixture
def client(monkeypatch):
    """Flask test client with MCP forced on."""
    monkeypatch.setattr("forge.app.MCP_ENABLED", True)
    monkeypatch.setattr("forge.app.MCP_AUTO_SYNC_ENABLED", True)
    from forge.app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ── /api/mcp/status ──────────────────────────────────────────────────────


class TestStatus:
    def test_status_cold_returns_unknown_without_live(self, client):
        """First hit, no ?live flag → reachable=null, no subprocess spawned."""
        with patch("forge.app._ping_mcp_server") as ping:
            r = client.get("/api/mcp/status")
        assert r.status_code == 200
        ping.assert_not_called()
        data = r.get_json()
        assert data["enabled"] is True
        assert set(data["servers"]) == {"blender", "salesforce", "disabled_srv"}
        assert data["servers"]["blender"]["reachable"] is None
        assert data["servers"]["blender"]["enabled"] is True

    def test_status_disabled_server_never_pings(self, client):
        """Disabled servers return reachable=null + enabled=false, never spawn."""
        fake = {
            "namespace": "blender",
            "enabled": True,
            "reachable": True,
            "ping_ms": 10,
            "tool_count": 1,
            "last_error": None,
            "last_checked_at": 1.0,
        }
        with patch("forge.app._ping_mcp_server", return_value=fake) as ping:
            r = client.get("/api/mcp/status?live=true")
        data = r.get_json()
        disabled = data["servers"]["disabled_srv"]
        assert disabled["enabled"] is False
        assert disabled["reachable"] is None
        called_names = [call.args[0] for call in ping.call_args_list]
        assert "disabled_srv" not in called_names

    def test_status_live_pings_and_caches(self, client):
        """live=true triggers ping; subsequent non-live hit uses cache."""
        fake = {
            "namespace": "blender",
            "enabled": True,
            "reachable": True,
            "ping_ms": 243,
            "tool_count": 18,
            "last_error": None,
            "last_checked_at": 1700000000.0,
        }
        with patch("forge.app._ping_mcp_server", return_value=fake) as ping:
            r1 = client.get("/api/mcp/status?namespace=blender&live=true")
            assert r1.status_code == 200
            body1 = r1.get_json()
            assert body1["servers"]["blender"]["reachable"] is True
            assert body1["servers"]["blender"]["tool_count"] == 18
            # Cached — second call must not re-ping
            r2 = client.get("/api/mcp/status?namespace=blender")
            body2 = r2.get_json()
            assert body2["servers"]["blender"]["reachable"] is True
        assert ping.call_count == 1

    def test_status_namespace_filter_404s_unknown(self, client):
        r = client.get("/api/mcp/status?namespace=ghost")
        assert r.status_code == 404
        data = r.get_json()
        assert "Unknown namespace" in data["error"]
        assert "blender" in data["suggestion"]

    def test_status_namespace_filter_scopes_response(self, client):
        r = client.get("/api/mcp/status?namespace=salesforce")
        data = r.get_json()
        assert list(data["servers"]) == ["salesforce"]

    def test_status_mcp_disabled(self, client, monkeypatch):
        monkeypatch.setattr("forge.app.MCP_ENABLED", False)
        r = client.get("/api/mcp/status")
        assert r.status_code == 200
        data = r.get_json()
        assert data["enabled"] is False
        assert data["servers"] == {}

    def test_status_cached_result_excludes_internal_fields(self, client):
        """_cached_at must never leak to the response body."""
        fake = {
            "namespace": "blender",
            "enabled": True,
            "reachable": True,
            "ping_ms": 100,
            "tool_count": 3,
            "last_error": None,
            "last_checked_at": 1700000000.0,
        }
        with patch("forge.app._ping_mcp_server", return_value=fake):
            client.get("/api/mcp/status?namespace=blender&live=true")
            r = client.get("/api/mcp/status?namespace=blender")
        data = r.get_json()
        assert "_cached_at" not in data["servers"]["blender"]

    def test_status_live_refetches_after_forced(self, client):
        """live=true bypasses the cache even when fresh."""
        fake1 = {"namespace": "blender", "enabled": True, "reachable": True,
                 "ping_ms": 100, "tool_count": 3, "last_error": None,
                 "last_checked_at": 1.0}
        fake2 = {"namespace": "blender", "enabled": True, "reachable": False,
                 "ping_ms": 500, "tool_count": 0, "last_error": "boom",
                 "last_checked_at": 2.0}
        with patch("forge.app._ping_mcp_server", side_effect=[fake1, fake2]) as ping:
            client.get("/api/mcp/status?namespace=blender&live=true")
            client.get("/api/mcp/status?namespace=blender&live=true")
        assert ping.call_count == 2


# ── _ping_mcp_server unit tests ──────────────────────────────────────────


class TestPingHelper:
    def test_ping_returns_tool_count_on_success(self):
        from forge.app import _ping_mcp_server
        raw = json.dumps({"tools": [{"name": "a"}, {"name": "b"}, {"name": "c"}]})
        with patch("forge.mcp_client.list_mcp_tools", return_value=raw):
            out = _ping_mcp_server("blender", {"command": ["uvx", "blender-mcp"], "enabled": True})
        assert out["reachable"] is True
        assert out["tool_count"] == 3
        assert out["last_error"] is None
        assert isinstance(out["ping_ms"], int)

    def test_ping_surfaces_command_error(self):
        from forge.app import _ping_mcp_server
        raw = json.dumps({"error": "MCP command not found: uvx"})
        with patch("forge.mcp_client.list_mcp_tools", return_value=raw):
            out = _ping_mcp_server("blender", {"command": ["uvx", "blender-mcp"], "enabled": True})
        assert out["reachable"] is False
        assert "not found" in out["last_error"]

    def test_ping_handles_missing_command(self):
        from forge.app import _ping_mcp_server
        out = _ping_mcp_server("ghost", {"command": [], "enabled": True})
        assert out["reachable"] is False
        assert out["last_error"] == "no command configured"
        assert out["ping_ms"] is None

    def test_ping_swallows_exceptions(self):
        from forge.app import _ping_mcp_server
        with patch("forge.mcp_client.list_mcp_tools", side_effect=RuntimeError("kaboom")):
            out = _ping_mcp_server("blender", {"command": ["uvx", "blender-mcp"], "enabled": True})
        assert out["reachable"] is False
        assert "RuntimeError" in out["last_error"]


# ── /api/mcp/namespaces ──────────────────────────────────────────────────


class TestNamespaces:
    def test_namespaces_returns_internal_and_external(self, client):
        r = client.get("/api/mcp/namespaces")
        assert r.status_code == 200
        data = r.get_json()
        assert data["enabled"] is True
        assert "forge:vault" in data["internal"]
        assert "forge:graph" in data["internal"]
        ns_names = {e["namespace"] for e in data["external"]}
        assert ns_names == {"blender", "salesforce", "disabled_srv"}

    def test_namespaces_active_includes_only_enabled_external(self, client):
        r = client.get("/api/mcp/namespaces")
        data = r.get_json()
        active = set(data["active_namespaces"])
        assert "forge:vault" in active
        assert "forge:graph" in active
        assert "blender" in active
        assert "salesforce" in active
        assert "disabled_srv" not in active

    def test_namespaces_summary_matches_router_banner(self, client):
        r = client.get("/api/mcp/namespaces")
        data = r.get_json()
        summary = data["summary"]
        assert "forge:vault" in summary
        assert "forge:graph" in summary
        assert "blender" in summary
        assert "salesforce" in summary

    def test_namespaces_vault_reflects_stores(self, client):
        """Writing to forge:vault shows up in recent_topics."""
        from forge.mcp_client import mcp_store
        mcp_store("forge:vault", "test-topic-alpha", "content-alpha")
        mcp_store("forge:vault", "test-topic-beta", "content-beta")
        r = client.get("/api/mcp/namespaces")
        data = r.get_json()
        vault = data["internal"]["forge:vault"]
        assert vault["entry_count"] >= 2
        topics = {t["topic"] for t in vault["recent_topics"]}
        assert {"test-topic-alpha", "test-topic-beta"}.issubset(topics)

    def test_namespaces_graph_reflects_stores(self, client):
        """Writing to forge:graph updates node_count + recent_nodes."""
        from forge.mcp_client import mcp_store
        mcp_store("forge:graph", "node-one", "first")
        mcp_store("forge:graph", "node-two", "second")
        r = client.get("/api/mcp/namespaces")
        data = r.get_json()
        graph = data["internal"]["forge:graph"]
        assert graph["node_count"] >= 2
        node_ids = {n["id"] for n in graph["recent_nodes"]}
        assert {"node-one", "node-two"}.issubset(node_ids)

    def test_namespaces_external_carries_config(self, client):
        r = client.get("/api/mcp/namespaces")
        data = r.get_json()
        blender = next(e for e in data["external"] if e["namespace"] == "blender")
        assert blender["enabled"] is True
        assert blender["command"] == ["uvx", "blender-mcp"]
        assert blender["timeout"] == 30.0

    def test_namespaces_mcp_disabled(self, client, monkeypatch):
        monkeypatch.setattr("forge.app.MCP_ENABLED", False)
        r = client.get("/api/mcp/namespaces")
        data = r.get_json()
        assert data == {"enabled": False}

    def test_namespaces_no_subprocess_spawn(self, client):
        """Sidebar refresh must be fast: no list_mcp_tools / call_mcp_tool hits."""
        with patch("forge.mcp_client.list_mcp_tools") as ping, \
             patch("forge.mcp_client.call_mcp_tool") as call:
            r = client.get("/api/mcp/namespaces")
        assert r.status_code == 200
        ping.assert_not_called()
        call.assert_not_called()
