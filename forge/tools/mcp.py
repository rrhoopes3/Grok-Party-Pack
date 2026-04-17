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

from .registry import ToolRegistry
from forge.mcp_client import mcp_store as _mcp_store
from forge.mcp_client import mcp_recall as _mcp_recall


def mcp_store(namespace: str, key: str, value: str) -> str:
    """Store a value in an MCP namespace (internal or external)."""
    return _mcp_store(namespace, key, value)


def mcp_recall(namespace: str, query: str, limit: int = 5) -> str:
    """Recall entries matching `query` from an MCP namespace."""
    return _mcp_recall(namespace, query, limit=limit)


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
