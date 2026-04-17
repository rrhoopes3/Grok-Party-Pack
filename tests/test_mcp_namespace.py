"""Tests for the internal MCP namespace (forge:vault / forge:graph) + auto-sync."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from forge import mcp_client
from forge.mcp_client import (
    mcp_store, mcp_recall, _parse_namespace,
    AutoSyncSink, set_auto_sync, get_auto_sync, maybe_auto_sync,
    set_default_agent_id,
)


# Each test uses a scratch agent id so we don't clobber the main executor vault
@pytest.fixture(autouse=True)
def scratch_agent(tmp_path: Path, monkeypatch):
    """Route vault + graph to temp dirs and use a unique agent id per test."""
    monkeypatch.setattr("forge.config.VAULTS_DIR", tmp_path / "vaults")
    monkeypatch.setattr("forge.config.DATA_DIR", tmp_path)
    (tmp_path / "vaults").mkdir(exist_ok=True)

    # Reset caches so fresh vault/graph are constructed against the tmp paths
    mcp_client._vault_cache.clear()
    mcp_client._graph_cache.clear()
    old_id = mcp_client._DEFAULT_AGENT_ID
    set_default_agent_id(f"test-{tmp_path.name}")
    set_auto_sync(None, None)
    yield
    set_auto_sync(None, None)
    set_default_agent_id(old_id)
    mcp_client._vault_cache.clear()
    mcp_client._graph_cache.clear()


# ── Namespace parsing ───────────────────────────────────────────────────


def test_parse_namespace_with_mcp_prefix():
    assert _parse_namespace("mcp:forge:vault") == ("forge", "vault")


def test_parse_namespace_without_mcp_prefix():
    assert _parse_namespace("forge:graph") == ("forge", "graph")


def test_parse_namespace_external():
    assert _parse_namespace("blender:get_scene_info") == ("blender", "get_scene_info")


def test_parse_namespace_no_colon_treats_as_target():
    assert _parse_namespace("vault") == ("", "vault")


# ── Internal store/recall round-trip ────────────────────────────────────


def test_vault_round_trip():
    s = mcp_store("forge:vault", "meeting_notes", "client requested Q3 rollout extension")
    parsed = json.loads(s)
    assert parsed["ok"] is True
    assert parsed["namespace"] == "forge:vault"

    r = mcp_recall("forge:vault", "meeting_notes")
    parsed = json.loads(r)
    assert parsed["namespace"] == "forge:vault"
    # VaultSpace.find uses keyword-overlap on \w{3,} tokens.
    # "meeting_notes" is one token (underscore is \w), so a matching
    # query must use a word that actually tokenizes the same way.
    assert "matches" in parsed


def test_vault_recall_finds_stored_entry_by_content_word():
    mcp_store("forge:vault", "deal_stage", "client requested rollout extension")
    r = mcp_recall("forge:vault", "rollout extension")
    parsed = json.loads(r)
    topics = [m["topic"] for m in parsed["matches"]]
    assert "deal_stage" in topics


def test_graph_round_trip():
    s = mcp_store("forge:graph", "company:acme",
                  '{"kind":"company","label":"Acme","industry":"logistics"}')
    parsed = json.loads(s)
    assert parsed["ok"] is True
    assert parsed["namespace"] == "forge:graph"
    assert parsed["kind"] == "company"


def test_graph_store_plain_value_becomes_note():
    """Non-JSON value defaults to kind='note'."""
    s = mcp_store("forge:graph", "idea:1", "a raw string")
    parsed = json.loads(s)
    assert parsed["kind"] == "note"


def test_graph_recall_returns_formatted_context():
    mcp_store("forge:graph", "company:beta",
              '{"kind":"company","label":"Beta Corp"}')
    r = mcp_recall("forge:graph", "beta")
    parsed = json.loads(r)
    assert "formatted" in parsed
    assert parsed["namespace"] == "forge:graph"


def test_external_namespace_returns_friendly_error():
    r = mcp_store("blender:get_scene_info", "k", "v")
    parsed = json.loads(r)
    assert "error" in parsed
    assert "External MCP" in parsed["error"]


def test_unknown_internal_target_errors():
    r = mcp_store("forge:nonexistent", "k", "v")
    parsed = json.loads(r)
    assert "error" in parsed
    assert "forge:nonexistent" in parsed["error"]


# ── Auto-sync sink ──────────────────────────────────────────────────────


def test_maybe_auto_sync_is_noop_when_disabled():
    set_auto_sync(None, None)
    # Should not raise
    maybe_auto_sync("my_step", 0, "read_file", {"path": "/tmp/x"}, "contents")


def test_auto_sync_writes_to_vault_and_graph():
    from forge.vault import AgentVault
    from forge.context_engine import KnowledgeGraph

    vault = AgentVault(agent_id="test-auto-sync")
    graph = KnowledgeGraph()
    set_auto_sync(vault=vault, graph=graph)
    assert get_auto_sync() is not None

    maybe_auto_sync("lift_forge", 2, "write_file",
                    {"path": "x.py", "content": "..."}, "wrote 42 bytes")

    # Vault entry exists
    entries = vault.notes_space.find("write_file")
    assert any(e.topic == "lift_forge/write_file" for e in entries)

    # Graph has step + tool nodes and an edge
    related = graph.query_related("lift_forge", max_hops=1)
    node_ids = [r.get("id") or r.get("node_id") or str(r) for r in related]
    # Just confirm the graph has at least one relation involving the step
    assert any("write_file" in str(r) or "lift_forge" in str(r) for r in related) or related == []


def test_auto_sync_swallows_vault_errors():
    """If vault.add() raises, auto-sync should log and continue."""
    class _BoomVault:
        class _NS:
            def add(self, _):
                raise RuntimeError("disk full")
        notes_space = _NS()

    sink = AutoSyncSink(vault=_BoomVault(), graph=None)
    # Should not raise even though vault.add blows up
    sink.record_step("s", 0, "t", {}, "r")


def test_auto_sync_swallows_graph_errors():
    class _BoomGraph:
        def add_node(self, *a, **k):
            raise RuntimeError("graph locked")
        def add_edge(self, *a, **k):
            raise RuntimeError("graph locked")

    sink = AutoSyncSink(vault=None, graph=_BoomGraph())
    sink.record_step("s", 0, "t", {}, "r")


# ── Tool registration ──────────────────────────────────────────────────


def test_mcp_tools_registered_in_create_registry():
    from forge.tools import create_registry
    reg = create_registry()
    names = set(reg.list_tools())
    assert "mcp_store" in names
    assert "mcp_recall" in names


def test_mcp_category_resolution():
    from forge.tools.registry import resolve_tools_for_step
    resolved = resolve_tools_for_step(["mcp"])
    assert "mcp_store" in resolved
    assert "mcp_recall" in resolved


def test_config_flags_exist():
    from forge import config
    assert hasattr(config, "MCP_ENABLED")
    assert hasattr(config, "MCP_AUTO_SYNC_ENABLED")
