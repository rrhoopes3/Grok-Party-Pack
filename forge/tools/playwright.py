"""Playwright tool — drives @playwright/mcp via Microsoft's official MCP server.

Requires:
  1. Node.js + npm on PATH so `npx @playwright/mcp@latest` can run
  2. First run downloads Chromium (~150MB); subsequent runs reuse the cache

Two layers, mirroring the blender pack:
  - Convenience wrappers for the most common ops (navigate, screenshot,
    click, fill, snapshot, eval) — terse agent calls
  - A generic `playwright_call_tool` passthrough so the full @playwright/mcp
    surface (hover, drag, new_tab, network_requests, console_messages, ...)
    is reachable without hard-coding every signature
"""
from __future__ import annotations

import json

from .registry import ToolRegistry
from forge.mcp_client import call_mcp_tool, list_mcp_tools


_SERVER_COMMAND = "npx"
_SERVER_ARGS = ["-y", "@playwright/mcp@latest"]
_DEFAULT_TIMEOUT = 90.0  # first-run Chromium download can take a while


def playwright_list_tools() -> str:
    """Enumerate every tool the @playwright/mcp server exposes."""
    return list_mcp_tools(command=_SERVER_COMMAND, args=_SERVER_ARGS)


def playwright_call_tool(tool_name: str, arguments_json: str = "{}") -> str:
    """Call any @playwright/mcp tool by name with JSON-encoded arguments."""
    try:
        tool_args = json.loads(arguments_json) if arguments_json else {}
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"arguments_json must be valid JSON: {e}"})
    if not isinstance(tool_args, dict):
        return json.dumps({"error": "arguments_json must be a JSON object"})
    return call_mcp_tool(
        command=_SERVER_COMMAND,
        args=_SERVER_ARGS,
        tool_name=tool_name,
        tool_args=tool_args,
        timeout=_DEFAULT_TIMEOUT,
    )


def playwright_navigate(url: str) -> str:
    """Navigate the browser to a URL."""
    return call_mcp_tool(
        command=_SERVER_COMMAND,
        args=_SERVER_ARGS,
        tool_name="browser_navigate",
        tool_args={"url": url},
        timeout=_DEFAULT_TIMEOUT,
    )


def playwright_snapshot() -> str:
    """Accessibility-tree snapshot of the current page — text + element refs."""
    return call_mcp_tool(
        command=_SERVER_COMMAND,
        args=_SERVER_ARGS,
        tool_name="browser_snapshot",
        tool_args={},
        timeout=_DEFAULT_TIMEOUT,
    )


def playwright_screenshot() -> str:
    """Capture the current page as a base64-encoded PNG."""
    return call_mcp_tool(
        command=_SERVER_COMMAND,
        args=_SERVER_ARGS,
        tool_name="browser_take_screenshot",
        tool_args={},
        timeout=_DEFAULT_TIMEOUT,
    )


def playwright_click(element: str, ref: str) -> str:
    """Click an element. `ref` is the element uid from a prior snapshot."""
    return call_mcp_tool(
        command=_SERVER_COMMAND,
        args=_SERVER_ARGS,
        tool_name="browser_click",
        tool_args={"element": element, "ref": ref},
        timeout=_DEFAULT_TIMEOUT,
    )


def playwright_fill(element: str, ref: str, text: str) -> str:
    """Fill a text field. `ref` is the element uid from a prior snapshot."""
    return call_mcp_tool(
        command=_SERVER_COMMAND,
        args=_SERVER_ARGS,
        tool_name="browser_type",
        tool_args={"element": element, "ref": ref, "text": text},
        timeout=_DEFAULT_TIMEOUT,
    )


def playwright_eval(expression: str) -> str:
    """Run arbitrary JS in the page context for reading state / debugging."""
    return call_mcp_tool(
        command=_SERVER_COMMAND,
        args=_SERVER_ARGS,
        tool_name="browser_evaluate",
        tool_args={"function": f"() => ({expression})"},
        timeout=_DEFAULT_TIMEOUT,
    )


def playwright_close() -> str:
    """Close the browser. Free up the subprocess / Chromium instance."""
    return call_mcp_tool(
        command=_SERVER_COMMAND,
        args=_SERVER_ARGS,
        tool_name="browser_close",
        tool_args={},
        timeout=_DEFAULT_TIMEOUT,
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        name="playwright_list_tools",
        description=(
            "List every tool exposed by the @playwright/mcp server. Use this "
            "first if a convenience wrapper below doesn't fit — then call "
            "playwright_call_tool with the exact tool name."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        handler=playwright_list_tools,
    )
    registry.register(
        name="playwright_call_tool",
        description=(
            "Generic passthrough to any @playwright/mcp tool. Pass the tool "
            "name and a JSON-encoded object of arguments. Use "
            "playwright_list_tools first to see exact schemas."
        ),
        parameters={
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Exact @playwright/mcp tool name, e.g. 'browser_hover'",
                },
                "arguments_json": {
                    "type": "string",
                    "description": "JSON-encoded object of arguments for the tool",
                },
            },
            "required": ["tool_name"],
        },
        handler=playwright_call_tool,
    )
    registry.register(
        name="playwright_navigate",
        description="Navigate the browser to a URL. Loads the page and waits for it to settle.",
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Full URL (http:// or https://)"}},
            "required": ["url"],
        },
        handler=playwright_navigate,
    )
    registry.register(
        name="playwright_snapshot",
        description=(
            "Get an accessibility-tree snapshot of the current page. Returns "
            "text content plus element refs you can pass to click/fill. "
            "PREFERRED over screenshot for agent reasoning — it's text, not pixels."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        handler=playwright_snapshot,
    )
    registry.register(
        name="playwright_screenshot",
        description=(
            "Capture the page as base64 PNG. Use for vision-model feedback "
            "loops or when layout/color matters. For text extraction use "
            "playwright_snapshot instead."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        handler=playwright_screenshot,
    )
    registry.register(
        name="playwright_click",
        description=(
            "Click an element identified by `ref` from a prior snapshot. "
            "Pass a short human description in `element` for logging."
        ),
        parameters={
            "type": "object",
            "properties": {
                "element": {"type": "string", "description": "Human description, e.g. 'Sign in button'"},
                "ref": {"type": "string", "description": "Element uid from the latest playwright_snapshot"},
            },
            "required": ["element", "ref"],
        },
        handler=playwright_click,
    )
    registry.register(
        name="playwright_fill",
        description="Type text into a form field identified by `ref` from a prior snapshot.",
        parameters={
            "type": "object",
            "properties": {
                "element": {"type": "string", "description": "Human description, e.g. 'Email input'"},
                "ref": {"type": "string", "description": "Element uid from the latest playwright_snapshot"},
                "text": {"type": "string", "description": "Text to type"},
            },
            "required": ["element", "ref", "text"],
        },
        handler=playwright_fill,
    )
    registry.register(
        name="playwright_eval",
        description=(
            "Evaluate a JavaScript expression in the page context. "
            "Good for reading state, computing values, simple scraping. "
            "Do NOT use to implement UI changes — those won't persist."
        ),
        parameters={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "JS expression to return, e.g. 'document.title' or 'window.location.href'",
                }
            },
            "required": ["expression"],
        },
        handler=playwright_eval,
    )
    registry.register(
        name="playwright_close",
        description="Close the Playwright browser. Frees the Chromium subprocess.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=playwright_close,
    )
