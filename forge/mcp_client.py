"""MCP (Model Context Protocol) client — multi-node surface for Forge agents.

Exposes two namespaces behind one API:

    external — any MCP server spawned over stdio (blender-mcp,
               @salesforce/mcp, arxiv-mcp, …)
    internal — Forge's own Vault and KnowledgeGraph, reachable via
               `forge:vault` / `forge:graph` namespaces so agents can
               store/recall memory through the same store/recall tool
               shape as external servers

Also hosts an optional auto-sync sink that records every successful
executor step into vault + graph, building up implicit memory as work
progresses. Hook it up once with `set_auto_sync(vault, graph)`; the
executor picks it up via `maybe_auto_sync` with no coupling.

Complements `forge.mcp_server` which exposes Forge's tool registry OUT
to external MCP clients (Claude Code, Cursor). This module goes the
other way: synchronous Forge tool handlers → async MCP calls into
external servers, OR direct calls into internal subsystems.

Per-call subprocess spawn (~1-3s overhead) for external. Fine for MVP;
can be upgraded to a persistent session pool later without changing
the synchronous facade.

Usage:
    from forge.mcp_client import call_mcp_tool, list_mcp_tools, mcp_store, mcp_recall

    text = call_mcp_tool(
        command="uvx",
        args=["blender-mcp"],
        tool_name="get_scene_info",
        tool_args={},
        timeout=60.0,
    )
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

log = logging.getLogger("forge.mcp_client")


def _import_mcp_sdk():
    """Lazy import so the module loads even if `mcp` isn't installed yet."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    return ClientSession, StdioServerParameters, stdio_client


def _serialize_content(items: list) -> str:
    """Flatten an MCP CallToolResult.content list into a single string.

    Content items are usually TextContent (.text) but can also be ImageContent
    or EmbeddedResource. We join textual parts and note non-text items so the
    LLM caller can react.
    """
    parts: list[str] = []
    for item in items:
        if hasattr(item, "text") and item.text is not None:
            parts.append(item.text)
        elif hasattr(item, "data") and hasattr(item, "mimeType"):
            # Image or blob — surface a pointer rather than dumping base64
            data = getattr(item, "data", "")
            size = len(data) if isinstance(data, (str, bytes)) else 0
            parts.append(f"[{item.mimeType} blob, {size} bytes base64]")
        else:
            parts.append(str(item))
    return "\n".join(parts) if parts else ""


async def _call_tool_async(
    command: str,
    args: list[str],
    tool_name: str,
    tool_args: dict[str, Any],
    timeout: float,
    init_timeout: float,
) -> dict[str, Any]:
    ClientSession, StdioServerParameters, stdio_client = _import_mcp_sdk()
    params = StdioServerParameters(command=command, args=args)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=init_timeout)
            result = await asyncio.wait_for(
                session.call_tool(tool_name, tool_args),
                timeout=timeout,
            )
            return {
                "isError": bool(getattr(result, "isError", False)),
                "content": _serialize_content(list(getattr(result, "content", []) or [])),
            }


async def _list_tools_async(
    command: str,
    args: list[str],
    init_timeout: float,
) -> list[dict[str, Any]]:
    ClientSession, StdioServerParameters, stdio_client = _import_mcp_sdk()
    params = StdioServerParameters(command=command, args=args)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=init_timeout)
            result = await asyncio.wait_for(session.list_tools(), timeout=init_timeout)
            tools = []
            for t in getattr(result, "tools", []) or []:
                tools.append({
                    "name": getattr(t, "name", ""),
                    "description": getattr(t, "description", "") or "",
                    "inputSchema": getattr(t, "inputSchema", None),
                })
            return tools


def call_mcp_tool(
    command: str,
    args: list[str],
    tool_name: str,
    tool_args: dict[str, Any],
    timeout: float = 60.0,
    init_timeout: float = 15.0,
) -> str:
    """Synchronous facade — spawn an MCP server, call one tool, return JSON string.

    Suitable for a Forge tool handler. Always returns a JSON-parseable string.
    """
    try:
        result = asyncio.run(
            _call_tool_async(command, args, tool_name, tool_args, timeout, init_timeout)
        )
        return json.dumps(result, default=str, indent=2)
    except asyncio.TimeoutError:
        return json.dumps({"error": f"MCP call timed out after {timeout}s", "tool": tool_name})
    except FileNotFoundError:
        return json.dumps({
            "error": f"MCP command not found: {command}. Ensure it's on PATH.",
            "tool": tool_name,
        })
    except Exception as e:
        log.exception("MCP call failed: %s / %s", command, tool_name)
        return json.dumps({"error": f"{type(e).__name__}: {e}", "tool": tool_name})


def list_mcp_tools(command: str, args: list[str], init_timeout: float = 15.0) -> str:
    """Enumerate tools exposed by an MCP server. Returns a JSON string."""
    try:
        tools = asyncio.run(_list_tools_async(command, args, init_timeout))
        return json.dumps({"tools": tools}, default=str, indent=2)
    except asyncio.TimeoutError:
        return json.dumps({"error": f"MCP list_tools timed out after {init_timeout}s"})
    except FileNotFoundError:
        return json.dumps({"error": f"MCP command not found: {command}"})
    except Exception as e:
        log.exception("MCP list_tools failed: %s", command)
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


# ── Internal MCP namespace (forge:vault / forge:graph) ──────────────────
#
# Presents Forge's own Vault and KnowledgeGraph as "internal" MCP
# namespaces so agents can read/write internal memory through the same
# mcp_store / mcp_recall shape they use for external MCP servers.
#
# Default agent id is "forge:executor" — every executor run shares one
# vault unless the caller flips it via set_default_agent_id().


_DEFAULT_AGENT_ID = "forge:executor"
_vault_cache: dict[str, Any] = {}
_graph_cache: dict[str, Any] = {}


def set_default_agent_id(agent_id: str) -> None:
    global _DEFAULT_AGENT_ID
    _DEFAULT_AGENT_ID = agent_id


def _get_vault(agent_id: str | None = None):
    from forge.vault import AgentVault
    aid = agent_id or _DEFAULT_AGENT_ID
    if aid not in _vault_cache:
        _vault_cache[aid] = AgentVault(agent_id=aid)
    return _vault_cache[aid]


def _get_graph(path_key: str = "default"):
    from forge.context_engine import KnowledgeGraph
    if path_key not in _graph_cache:
        _graph_cache[path_key] = KnowledgeGraph()
    return _graph_cache[path_key]


def _parse_namespace(namespace: str) -> tuple[str, str]:
    """Split `mcp:forge:vault` → ('forge', 'vault'). Returns (scope, target)."""
    ns = namespace.strip()
    if ns.startswith("mcp:"):
        ns = ns[4:]
    if ":" in ns:
        scope, target = ns.split(":", 1)
        return (scope, target)
    return ("", ns)


def _internal_store(target: str, key: str, value: str) -> dict[str, Any]:
    """Write to forge:vault or forge:graph."""
    if target == "vault":
        from forge.vault import VaultEntry
        vault = _get_vault()
        vault.notes_space.add(VaultEntry(topic=key, content=value, confidence=0.9))
        return {"ok": True, "namespace": "forge:vault", "key": key}
    if target == "graph":
        graph = _get_graph()
        kind = "note"
        label = key
        props: dict[str, Any] = {"content": value}
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                kind = parsed.get("kind", kind)
                label = parsed.get("label", label)
                props = {k: v for k, v in parsed.items() if k not in ("kind", "label")}
        except (json.JSONDecodeError, TypeError):
            pass
        graph.add_node(key, kind=kind, label=label, **props)
        return {"ok": True, "namespace": "forge:graph", "node_id": key, "kind": kind}
    return {"error": f"Unknown internal target: forge:{target}"}


def _internal_recall(target: str, query: str, limit: int = 5) -> dict[str, Any]:
    """Read from forge:vault or forge:graph."""
    if target == "vault":
        vault = _get_vault()
        entries = vault.notes_space.find(query, limit=limit)
        return {
            "namespace": "forge:vault",
            "query": query,
            "matches": [
                {
                    "topic": getattr(e, "topic", ""),
                    "content": getattr(e, "content", ""),
                    "confidence": round(getattr(e, "confidence", 0.0), 3),
                }
                for e in entries
            ],
        }
    if target == "graph":
        graph = _get_graph()
        return {
            "namespace": "forge:graph",
            "query": query,
            "formatted": graph.recall_graph_context(query, limit=limit),
        }
    return {"error": f"Unknown internal target: forge:{target}"}


def mcp_store(namespace: str, key: str, value: str) -> str:
    """Store `value` under `key` in the given MCP namespace.

    `namespace` is "forge:vault" or "forge:graph" for internal memory.
    External MCP servers with a store-like tool can be added later via
    config-driven dispatch; for now non-forge namespaces return an error.
    """
    scope, target = _parse_namespace(namespace)
    if scope == "forge":
        return json.dumps(_internal_store(target, key, value), default=str, indent=2)
    return json.dumps({
        "error": f"External MCP namespace routing not yet wired: {namespace}. "
                 f"Use blender_call_tool or add a config entry.",
    })


def mcp_recall(namespace: str, query: str, limit: int = 5) -> str:
    """Recall by `query` from the given MCP namespace.

    `namespace` is "forge:vault" or "forge:graph" for internal memory.
    """
    scope, target = _parse_namespace(namespace)
    if scope == "forge":
        return json.dumps(_internal_recall(target, query, limit=limit), default=str, indent=2)
    return json.dumps({
        "error": f"External MCP namespace routing not yet wired: {namespace}.",
    })


# ── Auto-sync sink (executor observer) ──────────────────────────────────


class AutoSyncSink:
    """Writes every successful executor step into vault + graph.

    Wired once at startup via `set_auto_sync(vault, graph)`; executor picks
    it up through the module-level `maybe_auto_sync` call. No coupling.
    """

    def __init__(self, vault=None, graph=None):
        self.vault = vault
        self.graph = graph

    def record_step(self, step_title: str, iteration: int, tool_name: str,
                    args: dict[str, Any], result: str) -> None:
        summary = result if isinstance(result, str) else str(result)
        if len(summary) > 200:
            summary = summary[:200] + "…"
        if self.vault is not None:
            try:
                from forge.vault import VaultEntry
                self.vault.notes_space.add(VaultEntry(
                    topic=f"{step_title}/{tool_name}",
                    content=f"iter={iteration} args={json.dumps(args, default=str)[:120]} result={summary}",
                    confidence=0.7,
                ))
            except Exception:
                log.exception("auto-sync vault.add failed")
        if self.graph is not None:
            try:
                self.graph.add_node(f"step:{step_title}", kind="step", label=step_title)
                self.graph.add_node(f"tool:{tool_name}", kind="tool", label=tool_name)
                self.graph.add_edge(f"step:{step_title}", f"tool:{tool_name}", relation="used")
            except Exception:
                log.exception("auto-sync graph.add failed")


_AUTO_SYNC_SINK: AutoSyncSink | None = None


def set_auto_sync(vault=None, graph=None) -> None:
    """Enable auto-sync globally. Pass None for both to disable."""
    global _AUTO_SYNC_SINK
    if vault is None and graph is None:
        _AUTO_SYNC_SINK = None
    else:
        _AUTO_SYNC_SINK = AutoSyncSink(vault=vault, graph=graph)


def get_auto_sync() -> AutoSyncSink | None:
    return _AUTO_SYNC_SINK


def maybe_auto_sync(step_title: str, iteration: int, tool_name: str,
                    args: dict[str, Any], result: str) -> None:
    """No-op if no sink wired. One-liner for executor hooks."""
    sink = _AUTO_SYNC_SINK
    if sink is not None:
        try:
            sink.record_step(step_title, iteration, tool_name, args, result)
        except Exception:
            log.exception("auto-sync record_step failed")


def enable_default_auto_sync() -> None:
    """Convenience — wire the default executor vault + shared graph."""
    set_auto_sync(vault=_get_vault(), graph=_get_graph())
