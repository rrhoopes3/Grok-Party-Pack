"""Blender tool — drives the blender-mcp server (https://github.com/ahujasid/blender-mcp).

Requires:
  1. Blender installed (Blender 5.1+ tested)
  2. blender-mcp addon.py installed inside Blender and enabled
     (Edit → Preferences → Add-ons → Install addon.py)
  3. In Blender's 3D View sidebar (press N), click "Connect to Claude"
     to open the TCP socket the MCP server talks to (default port 9876)
  4. `uv` installed and on PATH so `uvx blender-mcp` can launch the server
     on demand

Exposed tools give agents two layers:
  - Convenience wrappers (scene info, execute code, viewport screenshot) for
    the most common ops — these keep tool calls terse
  - A generic `blender_call_tool` passthrough so the full blender-mcp
    capability (Polyhaven, Sketchfab, Hyper3D Rodin, materials, textures,
    etc.) is reachable without hard-coding every signature
"""
from __future__ import annotations

import json
import os
from typing import Any

from .registry import ToolRegistry
from forge.mcp_client import call_mcp_tool, list_mcp_tools


_SERVER_COMMAND = "uvx"
_SERVER_ARGS = ["blender-mcp"]
_DEFAULT_TIMEOUT = 120.0  # 3D ops (asset download, Hyper3D gen) can be slow


def _server_env_args() -> list[str]:
    """Pass BLENDER_PORT through if user customized it."""
    args = list(_SERVER_ARGS)
    port = os.getenv("BLENDER_PORT")
    if port:
        # blender-mcp reads BLENDER_PORT from its own env, not CLI args.
        # The subprocess launched by mcp_client inherits this process's env,
        # so setting it here in the handler propagates.
        os.environ["BLENDER_PORT"] = port
    return args


# ── Handlers ────────────────────────────────────────────────────────────


def blender_list_tools() -> str:
    """Enumerate every tool the blender-mcp server exposes (names + schemas).

    Use this first to see what's available, then call with `blender_call_tool`.
    """
    return list_mcp_tools(command=_SERVER_COMMAND, args=_server_env_args())


def blender_call_tool(tool_name: str, arguments_json: str = "{}") -> str:
    """Call any blender-mcp tool by name with JSON-encoded arguments.

    Examples:
      blender_call_tool("search_polyhaven_assets", '{"asset_type": "hdris"}')
      blender_call_tool("generate_hyper3d_model_via_text", '{"text_prompt": "a red chair"}')
    """
    try:
        tool_args = json.loads(arguments_json) if arguments_json else {}
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"arguments_json must be valid JSON: {e}"})
    if not isinstance(tool_args, dict):
        return json.dumps({"error": "arguments_json must be a JSON object"})
    return call_mcp_tool(
        command=_SERVER_COMMAND,
        args=_server_env_args(),
        tool_name=tool_name,
        tool_args=tool_args,
        timeout=_DEFAULT_TIMEOUT,
    )


def blender_get_scene_info() -> str:
    """Fetch the current Blender scene overview (objects, lights, cameras)."""
    return call_mcp_tool(
        command=_SERVER_COMMAND,
        args=_server_env_args(),
        tool_name="get_scene_info",
        tool_args={},
        timeout=_DEFAULT_TIMEOUT,
    )


def blender_get_object_info(object_name: str) -> str:
    """Fetch details for a single object in the Blender scene."""
    return call_mcp_tool(
        command=_SERVER_COMMAND,
        args=_server_env_args(),
        tool_name="get_object_info",
        tool_args={"object_name": object_name},
        timeout=_DEFAULT_TIMEOUT,
    )


def blender_execute_code(code: str) -> str:
    """Run arbitrary Python code inside Blender (bpy is available)."""
    return call_mcp_tool(
        command=_SERVER_COMMAND,
        args=_server_env_args(),
        tool_name="execute_blender_code",
        tool_args={"code": code},
        timeout=_DEFAULT_TIMEOUT,
    )


def blender_viewport_screenshot() -> str:
    """Capture the current Blender viewport as a base64 image."""
    return call_mcp_tool(
        command=_SERVER_COMMAND,
        args=_server_env_args(),
        tool_name="get_viewport_screenshot",
        tool_args={},
        timeout=_DEFAULT_TIMEOUT,
    )


# ── Registration ────────────────────────────────────────────────────────


def register(registry: ToolRegistry) -> None:
    registry.register(
        name="blender_list_tools",
        description=(
            "List every tool exposed by the running blender-mcp server "
            "(names + descriptions + JSON schemas). Use this first to see "
            "what's available, then call specific tools with blender_call_tool."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        handler=blender_list_tools,
    )
    registry.register(
        name="blender_call_tool",
        description=(
            "Generic passthrough to any tool on the blender-mcp server. "
            "Pass the tool name and a JSON-encoded object of arguments. "
            "Use blender_list_tools first to see the exact schemas."
        ),
        parameters={
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Exact blender-mcp tool name, e.g. 'download_polyhaven_asset'",
                },
                "arguments_json": {
                    "type": "string",
                    "description": "JSON-encoded object of arguments for the tool",
                },
            },
            "required": ["tool_name"],
        },
        handler=blender_call_tool,
    )
    registry.register(
        name="blender_get_scene_info",
        description="Fetch the Blender scene overview: list of objects, lights, cameras, current frame.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=blender_get_scene_info,
    )
    registry.register(
        name="blender_get_object_info",
        description="Fetch details for a single object in the Blender scene by name.",
        parameters={
            "type": "object",
            "properties": {
                "object_name": {"type": "string", "description": "Blender object name, e.g. 'Cube'"},
            },
            "required": ["object_name"],
        },
        handler=blender_get_object_info,
    )
    registry.register(
        name="blender_execute_code",
        description=(
            "Execute arbitrary Python code inside Blender with full bpy access. "
            "Use for any operation without a dedicated tool: transforms, modifiers, "
            "materials, rendering, custom geometry, etc."
        ),
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code; `bpy` is already imported in Blender's context"},
            },
            "required": ["code"],
        },
        handler=blender_execute_code,
    )
    registry.register(
        name="blender_viewport_screenshot",
        description=(
            "Capture the current Blender viewport as a base64-encoded image. "
            "Use this to give vision-capable models a look at the scene state."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        handler=blender_viewport_screenshot,
    )
