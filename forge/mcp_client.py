"""MCP (Model Context Protocol) client — lets Forge drive external MCP servers.

Complements `forge.mcp_server` which exposes Forge's tool registry OUT to
external MCP clients (Claude Code, Cursor). This module goes the other way:
synchronous Forge tool handlers → async MCP calls into servers like
blender-mcp, @salesforce/mcp, etc.

Per-call subprocess spawn (~1-3s overhead). Fine for MVP; can be upgraded
to a persistent session pool later without changing the synchronous
facade exposed here.

Usage:
    from forge.mcp_client import call_mcp_tool, list_mcp_tools

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
