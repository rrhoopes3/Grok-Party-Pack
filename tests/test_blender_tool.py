"""Tests for forge.tools.blender + the underlying forge.mcp_client.

We don't spawn real MCP subprocesses. Tests mock the mcp_client layer directly
so they run in milliseconds and don't need Blender running.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from forge.tools import blender as blender_tool
from forge.tools.registry import ToolRegistry


# ── Registration ─────────────────────────────────────────────────────────


def test_all_blender_tools_register():
    reg = ToolRegistry()
    blender_tool.register(reg)
    names = set(reg.list_tools())
    expected = {
        "blender_list_tools",
        "blender_call_tool",
        "blender_get_scene_info",
        "blender_get_object_info",
        "blender_execute_code",
        "blender_viewport_screenshot",
    }
    assert expected.issubset(names)


def test_blender_tools_appear_in_create_registry():
    from forge.tools import create_registry
    reg = create_registry()
    names = set(reg.list_tools())
    assert "blender_execute_code" in names
    assert "blender_call_tool" in names


def test_blender_category_resolution():
    from forge.tools.registry import resolve_tools_for_step
    resolved = resolve_tools_for_step(["blender"])
    assert "blender_execute_code" in resolved
    assert "blender_get_scene_info" in resolved


# ── blender_call_tool argument parsing ───────────────────────────────────


def test_call_tool_rejects_non_json():
    with patch("forge.tools.blender.call_mcp_tool") as mock_call:
        out = blender_tool.blender_call_tool("get_scene_info", "{not json}")
    parsed = json.loads(out)
    assert "error" in parsed
    assert "valid JSON" in parsed["error"]
    mock_call.assert_not_called()


def test_call_tool_rejects_non_object_args():
    """arguments_json must be an object, not an array or scalar."""
    with patch("forge.tools.blender.call_mcp_tool") as mock_call:
        out = blender_tool.blender_call_tool("get_scene_info", "[1, 2, 3]")
    parsed = json.loads(out)
    assert "error" in parsed
    assert "JSON object" in parsed["error"]
    mock_call.assert_not_called()


def test_call_tool_forwards_to_mcp_client():
    expected = json.dumps({"isError": False, "content": "ok"})
    with patch("forge.tools.blender.call_mcp_tool", return_value=expected) as mock_call:
        out = blender_tool.blender_call_tool("get_scene_info", '{"foo": "bar"}')
    assert out == expected
    assert mock_call.call_count == 1
    kwargs = mock_call.call_args.kwargs
    assert kwargs["tool_name"] == "get_scene_info"
    assert kwargs["tool_args"] == {"foo": "bar"}


def test_call_tool_empty_args_defaults_to_empty_object():
    with patch("forge.tools.blender.call_mcp_tool", return_value="{}") as mock_call:
        blender_tool.blender_call_tool("get_scene_info", "")
    assert mock_call.call_args.kwargs["tool_args"] == {}


# ── Convenience wrappers ─────────────────────────────────────────────────


def test_execute_code_forwards_code():
    with patch("forge.tools.blender.call_mcp_tool", return_value="{}") as mock_call:
        blender_tool.blender_execute_code("bpy.ops.mesh.primitive_cube_add()")
    kwargs = mock_call.call_args.kwargs
    assert kwargs["tool_name"] == "execute_blender_code"
    assert kwargs["tool_args"] == {"code": "bpy.ops.mesh.primitive_cube_add()"}


def test_get_object_info_forwards_name():
    with patch("forge.tools.blender.call_mcp_tool", return_value="{}") as mock_call:
        blender_tool.blender_get_object_info("Cube")
    kwargs = mock_call.call_args.kwargs
    assert kwargs["tool_name"] == "get_object_info"
    assert kwargs["tool_args"] == {"object_name": "Cube"}


def test_list_tools_calls_list_mcp_tools():
    with patch("forge.tools.blender.list_mcp_tools", return_value='{"tools": []}') as mock_list:
        out = blender_tool.blender_list_tools()
    assert out == '{"tools": []}'
    mock_list.assert_called_once()


# ── mcp_client error handling ────────────────────────────────────────────


def test_mcp_client_command_not_found_returns_clean_error():
    from forge import mcp_client
    with patch("forge.mcp_client.asyncio.run", side_effect=FileNotFoundError()):
        out = mcp_client.call_mcp_tool(
            command="nonexistent-cmd",
            args=[],
            tool_name="anything",
            tool_args={},
        )
    parsed = json.loads(out)
    assert "error" in parsed
    assert "not found" in parsed["error"]
    assert parsed["tool"] == "anything"


def test_mcp_client_timeout_returns_clean_error():
    import asyncio
    from forge import mcp_client
    with patch("forge.mcp_client.asyncio.run", side_effect=asyncio.TimeoutError()):
        out = mcp_client.call_mcp_tool(
            command="slow-cmd",
            args=[],
            tool_name="hang",
            tool_args={},
            timeout=5.0,
        )
    parsed = json.loads(out)
    assert "error" in parsed
    assert "timed out" in parsed["error"]
    assert parsed["tool"] == "hang"


def test_mcp_client_generic_exception_returns_clean_error():
    from forge import mcp_client
    with patch("forge.mcp_client.asyncio.run", side_effect=RuntimeError("server died")):
        out = mcp_client.call_mcp_tool(
            command="cmd", args=[], tool_name="t", tool_args={},
        )
    parsed = json.loads(out)
    assert "error" in parsed
    assert "RuntimeError" in parsed["error"]
    assert "server died" in parsed["error"]


def test_list_mcp_tools_error_returns_clean_error():
    from forge import mcp_client
    with patch("forge.mcp_client.asyncio.run", side_effect=FileNotFoundError()):
        out = mcp_client.list_mcp_tools("nothing", [])
    parsed = json.loads(out)
    assert "error" in parsed
    assert "not found" in parsed["error"]


# ── Content serialization ────────────────────────────────────────────────


def test_serialize_content_handles_text_items():
    from forge.mcp_client import _serialize_content

    class _TextItem:
        def __init__(self, text):
            self.text = text

    out = _serialize_content([_TextItem("line one"), _TextItem("line two")])
    assert out == "line one\nline two"


def test_serialize_content_handles_image_items():
    from forge.mcp_client import _serialize_content

    class _ImageItem:
        data = "base64data" * 10
        mimeType = "image/png"

    out = _serialize_content([_ImageItem()])
    assert "image/png" in out
    assert "bytes base64" in out


def test_serialize_content_handles_empty():
    from forge.mcp_client import _serialize_content
    assert _serialize_content([]) == ""
