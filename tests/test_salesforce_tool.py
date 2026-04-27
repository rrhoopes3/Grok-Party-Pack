"""Tests for forge.tools.salesforce.

We don't invoke the real `sf` CLI in CI, so we stub subprocess.run / shutil.which.
"""
from __future__ import annotations

import json
import os
from unittest.mock import patch, MagicMock

import pytest

from forge.tools import salesforce as sf_tool
from forge.tools.registry import ToolRegistry


# ── Registration ─────────────────────────────────────────────────────────


def test_all_salesforce_tools_register():
    reg = ToolRegistry()
    sf_tool.register(reg)
    names = set(reg.list_tools())
    expected = {
        "salesforce_soql",
        "salesforce_describe",
        "salesforce_record_get",
        "salesforce_record_update",
        "salesforce_list_orgs",
    }
    assert expected.issubset(names)


def test_salesforce_tools_appear_in_create_registry():
    from forge.tools import create_registry
    reg = create_registry()
    names = set(reg.list_tools())
    assert "salesforce_soql" in names
    assert "salesforce_list_orgs" in names


def test_salesforce_cli_category_resolution():
    """CLI tools moved to `salesforce_cli` after the MCP router refactor."""
    from forge.tools.registry import resolve_tools_for_step
    resolved = resolve_tools_for_step(["salesforce_cli"])
    assert "salesforce_soql" in resolved
    assert "salesforce_describe" in resolved


def test_salesforce_category_now_routes_through_mcp():
    """Default `salesforce` category points at MCP router tools."""
    from forge.tools.registry import resolve_tools_for_step
    resolved = resolve_tools_for_step(["salesforce"])
    assert "salesforce_mcp_call" in resolved
    assert "mcp_call_tool" in resolved
    assert "salesforce_soql" not in resolved


# ── Handler behavior (no CLI) ────────────────────────────────────────────


def test_missing_sf_cli_returns_clear_error():
    """If `sf` isn't on PATH, return a helpful error instead of crashing."""
    with patch("forge.tools.salesforce.shutil.which", return_value=None):
        out = sf_tool.salesforce_soql("SELECT Id FROM Account LIMIT 1")
    parsed = json.loads(out)
    assert "error" in parsed
    assert "sf CLI not found" in parsed["error"]


def test_soql_success_path_parses_json():
    """Stub a successful sf invocation and verify result passthrough."""
    fake_stdout = json.dumps({
        "status": 0,
        "result": {"done": True, "totalSize": 1, "records": [{"Id": "001", "Name": "Acme"}]},
    })
    fake_proc = MagicMock(stdout=fake_stdout, stderr="", returncode=0)
    with patch("forge.tools.salesforce.shutil.which", return_value="/usr/local/bin/sf"), \
         patch("forge.tools.salesforce.subprocess.run", return_value=fake_proc):
        out = sf_tool.salesforce_soql("SELECT Id FROM Account LIMIT 1")
    parsed = json.loads(out)
    assert parsed["done"] is True
    assert parsed["records"][0]["Name"] == "Acme"


def test_record_update_blocked_without_writes_flag(monkeypatch):
    """Updates must be gated behind FORGE_SF_ALLOW_WRITES=true."""
    monkeypatch.delenv("FORGE_SF_ALLOW_WRITES", raising=False)
    out = sf_tool.salesforce_record_update("Opportunity", "006xx0000000001", '{"StageName": "Closed Won"}')
    parsed = json.loads(out)
    assert "error" in parsed
    assert "FORGE_SF_ALLOW_WRITES" in parsed["error"]


def test_record_update_rejects_non_object_values_json(monkeypatch):
    """values_json must be a JSON object, not an array or scalar."""
    monkeypatch.setenv("FORGE_SF_ALLOW_WRITES", "true")
    with patch("forge.tools.salesforce.shutil.which", return_value="/usr/local/bin/sf"):
        out = sf_tool.salesforce_record_update("Opportunity", "006xx", "[1, 2, 3]")
    parsed = json.loads(out)
    assert "error" in parsed
    assert "JSON object" in parsed["error"]


def test_record_update_rejects_malformed_values_json(monkeypatch):
    monkeypatch.setenv("FORGE_SF_ALLOW_WRITES", "true")
    with patch("forge.tools.salesforce.shutil.which", return_value="/usr/local/bin/sf"):
        out = sf_tool.salesforce_record_update("Opportunity", "006xx", "not json")
    parsed = json.loads(out)
    assert "error" in parsed


def test_timeout_returns_clear_error():
    import subprocess as _sub
    with patch("forge.tools.salesforce.shutil.which", return_value="/usr/local/bin/sf"), \
         patch("forge.tools.salesforce.subprocess.run", side_effect=_sub.TimeoutExpired(cmd="sf", timeout=90)):
        out = sf_tool.salesforce_soql("SELECT Id FROM Account")
    parsed = json.loads(out)
    assert "error" in parsed
    assert "timed out" in parsed["error"]


def test_non_json_stdout_surfaces_exit_and_raw():
    """When sf returns non-JSON (e.g. auth prompt), surface it legibly."""
    fake_proc = MagicMock(stdout="Please log in first\n", stderr="", returncode=1)
    with patch("forge.tools.salesforce.shutil.which", return_value="/usr/local/bin/sf"), \
         patch("forge.tools.salesforce.subprocess.run", return_value=fake_proc):
        out = sf_tool.salesforce_soql("SELECT Id FROM Account")
    parsed = json.loads(out)
    assert "error" in parsed
    assert "Please log in" in parsed["error"]
