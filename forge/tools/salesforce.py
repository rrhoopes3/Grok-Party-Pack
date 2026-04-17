"""Salesforce tool — thin wrapper over the `sf` CLI for personal productivity use.

Uses the user's existing sf CLI auth (run `sf org login web` before first use).
Read-only by default. Set FORGE_SF_ALLOW_WRITES=true to enable record updates.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

from .registry import ToolRegistry


_TIMEOUT = 90  # seconds
_MAX_BODY = 20_000  # chars — truncate large SOQL responses


def _run_sf(args: list[str], timeout: int = _TIMEOUT) -> dict[str, Any]:
    """Execute `sf <args> --json` and return the parsed response dict.

    Returns {"error": "..."} on any failure. On success, returns the raw
    parsed JSON (shape is sf-command-dependent; callers usually want
    response.get("result")).
    """
    if shutil.which("sf") is None:
        return {"error": "sf CLI not found — install Salesforce CLI (`npm i -g @salesforce/cli`) and run `sf org login web`"}
    cmd = ["sf", *args, "--json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"error": f"sf command timed out after {timeout}s: {' '.join(cmd)}"}
    except OSError as e:
        return {"error": f"sf invocation failed: {type(e).__name__}: {e}"}
    if not r.stdout.strip():
        stderr = r.stderr.strip() or "empty response"
        return {"error": f"sf returned no stdout (exit {r.returncode}): {stderr[:500]}"}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"error": f"sf returned non-JSON output (exit {r.returncode}): {r.stdout[:500]}"}


def _truncate(payload: dict[str, Any]) -> str:
    """Serialize + truncate the payload for tool-response use."""
    text = json.dumps(payload, default=str, indent=2)
    if len(text) > _MAX_BODY:
        text = text[:_MAX_BODY] + f"\n... [truncated at {_MAX_BODY} chars]"
    return text


# ── Handlers ────────────────────────────────────────────────────────────


def salesforce_soql(query: str, org: str = "") -> str:
    """Run a SOQL query. Read-only."""
    args = ["data", "query", "--query", query]
    if org:
        args += ["--target-org", org]
    resp = _run_sf(args)
    if "error" in resp:
        return _truncate(resp)
    return _truncate(resp.get("result", resp))


def salesforce_describe(sobject: str, org: str = "") -> str:
    """Describe an SObject's fields and metadata."""
    args = ["sobject", "describe", "--sobject", sobject]
    if org:
        args += ["--target-org", org]
    resp = _run_sf(args)
    if "error" in resp:
        return _truncate(resp)
    return _truncate(resp.get("result", resp))


def salesforce_record_get(sobject: str, record_id: str, org: str = "") -> str:
    """Fetch a single record by ID."""
    args = ["data", "get", "record", "--sobject", sobject, "--record-id", record_id]
    if org:
        args += ["--target-org", org]
    resp = _run_sf(args)
    if "error" in resp:
        return _truncate(resp)
    return _truncate(resp.get("result", resp))


def salesforce_record_update(sobject: str, record_id: str, values_json: str, org: str = "") -> str:
    """Update a record. Gated by FORGE_SF_ALLOW_WRITES=true.

    values_json is a JSON object string, e.g. '{"StageName": "Closed Won"}'.
    """
    if os.getenv("FORGE_SF_ALLOW_WRITES", "").strip().lower() != "true":
        return json.dumps({
            "error": "Writes disabled — set FORGE_SF_ALLOW_WRITES=true to enable record updates.",
        })
    try:
        values = json.loads(values_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"values_json must be a JSON object: {e}"})
    if not isinstance(values, dict):
        return json.dumps({"error": "values_json must be a JSON object, not a list or scalar"})
    # sf expects --values "Field1='x' Field2='y'" format. Quote each literal for safety.
    pairs = " ".join(f"{k}={json.dumps(str(v))}" for k, v in values.items())
    args = ["data", "update", "record", "--sobject", sobject, "--record-id", record_id, "--values", pairs]
    if org:
        args += ["--target-org", org]
    resp = _run_sf(args)
    if "error" in resp:
        return _truncate(resp)
    return _truncate(resp.get("result", resp))


def salesforce_list_orgs() -> str:
    """List all Salesforce orgs authenticated in the local sf CLI."""
    resp = _run_sf(["org", "list"])
    if "error" in resp:
        return _truncate(resp)
    return _truncate(resp.get("result", resp))


# ── Registration ────────────────────────────────────────────────────────


def register(registry: ToolRegistry) -> None:
    registry.register(
        name="salesforce_soql",
        description=(
            "Run a SOQL query against the authenticated Salesforce org. Read-only. "
            "Returns the parsed JSON result from `sf data query`."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "SOQL query, e.g. 'SELECT Id, Name FROM Account LIMIT 10'"},
                "org": {"type": "string", "description": "Optional org alias or username override"},
            },
            "required": ["query"],
        },
        handler=salesforce_soql,
    )
    registry.register(
        name="salesforce_describe",
        description="Describe a Salesforce SObject's fields, types, and metadata.",
        parameters={
            "type": "object",
            "properties": {
                "sobject": {"type": "string", "description": "SObject API name, e.g. Account, Opportunity, Contact"},
                "org": {"type": "string", "description": "Optional org alias or username override"},
            },
            "required": ["sobject"],
        },
        handler=salesforce_describe,
    )
    registry.register(
        name="salesforce_record_get",
        description="Fetch a single Salesforce record by SObject type and Id.",
        parameters={
            "type": "object",
            "properties": {
                "sobject": {"type": "string", "description": "SObject API name"},
                "record_id": {"type": "string", "description": "18-char Salesforce record Id"},
                "org": {"type": "string", "description": "Optional org alias or username override"},
            },
            "required": ["sobject", "record_id"],
        },
        handler=salesforce_record_get,
    )
    registry.register(
        name="salesforce_record_update",
        description=(
            "Update fields on a Salesforce record. Gated by FORGE_SF_ALLOW_WRITES=true. "
            "values_json is a JSON object of fields to set, e.g. '{\"StageName\": \"Closed Won\"}'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "sobject": {"type": "string", "description": "SObject API name"},
                "record_id": {"type": "string", "description": "18-char Salesforce record Id"},
                "values_json": {"type": "string", "description": "JSON object of fields to update"},
                "org": {"type": "string", "description": "Optional org alias or username override"},
            },
            "required": ["sobject", "record_id", "values_json"],
        },
        handler=salesforce_record_update,
    )
    registry.register(
        name="salesforce_list_orgs",
        description="List all Salesforce orgs authenticated in the local sf CLI.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=salesforce_list_orgs,
    )
