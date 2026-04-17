"""MCP namespace tools — unified store/recall across internal + external nodes.

Exposes the multi-node MCP client's namespace routing as first-class agent
tools. An agent can write to Forge's own Vault or KnowledgeGraph (the
"internal" namespaces) through the same shape it would use to hit an
external MCP server — once we wire external-namespace routing.

Internal namespaces (available now):
    forge:vault   — agent memory (keyed notes, keyword-searchable)
    forge:graph   — knowledge graph (nodes, edges, BFS recall)

External namespaces (future): routed per config-driven dispatch table.
"""
from __future__ import annotations

import json

from .registry import ToolRegistry
from forge.mcp_client import mcp_store as _mcp_store
from forge.mcp_client import mcp_recall as _mcp_recall
from forge.mcp_client import route_call_tool, route_list_tools, get_router


def mcp_store(namespace: str, key: str, value: str) -> str:
    """Store a value in an MCP namespace (internal or external)."""
    return _mcp_store(namespace, key, value)


def mcp_recall(namespace: str, query: str, limit: int = 5) -> str:
    """Recall entries matching `query` from an MCP namespace."""
    return _mcp_recall(namespace, query, limit=limit)


def mcp_call_tool(namespace: str, tool_name: str, args_json: str = "{}") -> str:
    """Generic dispatch — call any tool on any configured MCP namespace."""
    try:
        args = json.loads(args_json) if args_json else {}
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"args_json must be valid JSON: {e}"})
    if not isinstance(args, dict):
        return json.dumps({"error": "args_json must be a JSON object"})
    return route_call_tool(namespace, tool_name, args)


def mcp_list_tools(namespace: str) -> str:
    """List tools available in a namespace (internal introspection + external)."""
    return route_list_tools(namespace)


def mcp_list_namespaces() -> str:
    """List every namespace the router can route to (internal + active external)."""
    router = get_router()
    return json.dumps({
        "active": router.active_namespaces(),
        "configured_external": router.configured_servers(),
    }, indent=2)


def register(registry: ToolRegistry) -> None:
    registry.register(
        name="mcp_store",
        description=(
            "Store a value in an MCP namespace. Internal namespaces: "
            "'forge:vault' (agent notes), 'forge:graph' (knowledge graph). "
            "For forge:graph, `value` can be a JSON object with optional "
            "'kind' and 'label' fields to shape the node."
        ),
        parameters={
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Namespace like 'forge:vault' or 'forge:graph'",
                },
                "key": {"type": "string", "description": "Entry key (topic / node id)"},
                "value": {"type": "string", "description": "Value to store (string or JSON)"},
            },
            "required": ["namespace", "key", "value"],
        },
        handler=mcp_store,
    )
    registry.register(
        name="mcp_recall",
        description=(
            "Recall entries matching `query` from an MCP namespace. "
            "Internal namespaces: 'forge:vault' (keyword search over notes), "
            "'forge:graph' (BFS over nearest matching entity)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Namespace like 'forge:vault' or 'forge:graph'",
                },
                "query": {"type": "string", "description": "Keyword or entity query"},
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 5)",
                },
            },
            "required": ["namespace", "query"],
        },
        handler=mcp_recall,
    )
    registry.register(
        name="mcp_call_tool",
        description=(
            "Generic MCP dispatch — call any tool on any configured namespace. "
            "Internal: 'forge:vault' or 'forge:graph' with tool_name 'store' or "
            "'recall'. External: server name (e.g. 'blender') with the server's "
            "native tool name (e.g. 'get_scene_info'). `args_json` is a "
            "JSON-encoded object of arguments. Use mcp_list_tools first to see "
            "the exact schema for a given namespace."
        ),
        parameters={
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "e.g. 'forge:vault', 'blender'"},
                "tool_name": {"type": "string", "description": "Tool name within the namespace"},
                "args_json": {
                    "type": "string",
                    "description": "JSON object of arguments (default '{}')",
                },
            },
            "required": ["namespace", "tool_name"],
        },
        handler=mcp_call_tool,
    )
    registry.register(
        name="mcp_list_tools",
        description=(
            "List tools available in an MCP namespace. Internal namespaces "
            "return their fixed store/recall verbs; external namespaces spawn "
            "the server and enumerate its tools."
        ),
        parameters={
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "e.g. 'forge:vault', 'blender'"},
            },
            "required": ["namespace"],
        },
        handler=mcp_list_tools,
    )
    registry.register(
        name="mcp_list_namespaces",
        description=(
            "List every MCP namespace the router knows about: active "
            "(internal + enabled external) plus the full configured external "
            "map with enabled/auto_start/timeout per server."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        handler=mcp_list_namespaces,
    )
