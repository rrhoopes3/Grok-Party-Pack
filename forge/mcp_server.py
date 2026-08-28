"""
MCP (Model Context Protocol) Server for The Forge.

Exposes the Forge's 40+ tool registry as an MCP-compliant stdio server,
allowing any MCP client (Claude Code, Cursor, Windsurf, etc.) to use
Forge tools natively.

Usage (stdio transport — default):
    python -m forge.mcp_server

Usage (SSE transport — HTTP):
    python -m forge.mcp_server --transport sse --port 8420

Add to Claude Code's MCP config (~/.claude/settings.json):
    {
      "mcpServers": {
        "forge": {
          "command": "python",
          "args": ["-m", "forge.mcp_server"],
          "cwd": "/path/to/grok-party-pack"
        }
      }
    }
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

log = logging.getLogger("forge.mcp_server")

# ── MCP Protocol Constants ────────────────────────────────────────────────

JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2024-11-05"

SERVER_INFO = {
    "name": "forge",
    "version": "1.0.0",
}

SERVER_CAPABILITIES = {
    "tools": {"listChanged": False},
}


# ── Registry Bridge ──────────────────────────────────────────────────────

_registry = None


def _get_registry():
    """Lazy-load the Forge tool registry."""
    global _registry
    if _registry is None:
        from forge.tools import create_registry
        _registry = create_registry()
    return _registry


def _forge_tools_to_mcp() -> list[dict]:
    """Convert Forge raw tool schemas to MCP tool format."""
    registry = _get_registry()
    mcp_tools = []
    for tool in registry.get_raw_tools():
        mcp_tools.append({
            "name": tool["name"],
            "description": tool["description"],
            "inputSchema": tool["parameters"],
        })
    return mcp_tools


def _call_forge_tool(name: str, arguments: dict, sandbox_path: str = "") -> str:
    """Execute a Forge tool and return the result string."""
    registry = _get_registry()
    return registry.execute(name, arguments, sandbox_path=sandbox_path)


# ── JSON-RPC Helpers ─────────────────────────────────────────────────────

def _success(id: Any, result: dict) -> dict:
    return {"jsonrpc": JSONRPC_VERSION, "id": id, "result": result}


def _error(id: Any, code: int, message: str, data: Any = None) -> dict:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": id, "error": err}


# ── Request Handler ──────────────────────────────────────────────────────

def handle_request(request: dict, sandbox_path: str = "") -> dict | None:
    """Handle a single JSON-RPC request. Returns response dict or None for notifications."""
    method = request.get("method", "")
    id = request.get("id")
    params = request.get("params", {})

    # Notifications (no id) — no response required
    if id is None:
        if method == "notifications/initialized":
            log.info("Client initialized")
        return None

    if method == "initialize":
        return _success(id, {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": SERVER_CAPABILITIES,
            "serverInfo": SERVER_INFO,
        })

    elif method == "tools/list":
        tools = _forge_tools_to_mcp()
        return _success(id, {"tools": tools})

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        try:
            result_str = _call_forge_tool(tool_name, arguments, sandbox_path)
            # Try to parse as JSON for structured output
            try:
                result_data = json.loads(result_str)
                text = json.dumps(result_data, indent=2)
            except (json.JSONDecodeError, TypeError):
                text = result_str

            is_error = False
            if isinstance(result_str, str):
                try:
                    parsed = json.loads(result_str)
                    is_error = "error" in parsed
                except (json.JSONDecodeError, TypeError):
                    pass

            return _success(id, {
                "content": [{"type": "text", "text": text}],
                "isError": is_error,
            })
        except Exception as e:
            log.exception("Tool %s failed", tool_name)
            return _success(id, {
                "content": [{"type": "text", "text": f"Error: {type(e).__name__}: {e}"}],
                "isError": True,
            })

    elif method == "ping":
        return _success(id, {})

    else:
        return _error(id, -32601, f"Method not found: {method}")


# ── Stdio Transport ──────────────────────────────────────────────────────

async def _read_message(reader: asyncio.StreamReader) -> dict | None:
    """Read a JSON-RPC message from stdin using Content-Length framing."""
    headers = {}
    while True:
        line = await reader.readline()
        if not line:
            return None  # EOF
        line_str = line.decode("utf-8").strip()
        if line_str == "":
            break  # End of headers
        if ":" in line_str:
            key, value = line_str.split(":", 1)
            headers[key.strip().lower()] = value.strip()

    content_length = int(headers.get("content-length", 0))
    if content_length == 0:
        return None

    body = await reader.readexactly(content_length)
    return json.loads(body.decode("utf-8"))


def _write_message(data: dict) -> None:
    """Write a JSON-RPC message to stdout with Content-Length framing."""
    body = json.dumps(data)
    body_bytes = body.encode("utf-8")
    header = f"Content-Length: {len(body_bytes)}\r\n\r\n"
    sys.stdout.buffer.write(header.encode("utf-8"))
    sys.stdout.buffer.write(body_bytes)
    sys.stdout.buffer.flush()


async def run_stdio(sandbox_path: str = "") -> None:
    """Run the MCP server over stdio transport."""
    log.info("Forge MCP server starting (stdio)")
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin.buffer)

    while True:
        try:
            request = await _read_message(reader)
            if request is None:
                break  # EOF

            response = handle_request(request, sandbox_path)
            if response is not None:
                _write_message(response)
        except asyncio.IncompleteReadError:
            break
        except Exception as e:
            log.exception("Error processing message")
            # Try to send error response
            try:
                _write_message(_error(None, -32603, f"Internal error: {e}"))
            except Exception:
                break


# ── SSE Transport ─────────────────────────────────────────────────────────

def create_sse_app(sandbox_path: str = ""):
    """Create a Flask app for SSE transport (HTTP-based MCP)."""
    from flask import Flask, Response, request as flask_request, jsonify
    from forge.security import install_auth_gate, require_auth

    sse_app = Flask(__name__)
    install_auth_gate(sse_app, allow_loopback_demo=False)

    @sse_app.route("/sse", methods=["GET"])
    @require_auth
    def sse_stream():
        """SSE endpoint — client sends requests via POST /message, receives via this stream."""
        def generate():
            # Send initial endpoint info
            yield f"event: endpoint\ndata: /message\n\n"
        return Response(generate(), mimetype="text/event-stream")

    @sse_app.route("/message", methods=["POST"])
    @require_auth
    def handle_message():
        """Handle JSON-RPC request from SSE client."""
        data = flask_request.get_json()
        response = handle_request(data, sandbox_path)
        if response is None:
            return "", 204
        return jsonify(response)

    return sse_app


# ── CLI Entry Point ──────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Forge MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument("--sandbox", type=str, default="", help="Sandbox path for file operations")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(message)s",
        stream=sys.stderr,  # logs to stderr, not stdout (stdout is for MCP protocol)
    )

    if args.transport == "sse":
        app = create_sse_app(sandbox_path=args.sandbox)
        log.info("Forge MCP server starting (SSE on port %d)", args.port)
        from forge.security import bind_host
        app.run(host=bind_host(), port=args.port, debug=False)
    else:
        asyncio.run(run_stdio(sandbox_path=args.sandbox))


if __name__ == "__main__":
    main()
