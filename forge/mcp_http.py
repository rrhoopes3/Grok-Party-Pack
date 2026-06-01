"""
Forge MCP Server — HTTP/SSE Transport (with Authentication)

This version adds simple Bearer token authentication so you can safely
expose your Forge MCP server over Cloudflare Tunnel.

Usage:
    export FORGE_MCP_API_KEY="your-secret-key-here"
    uvicorn forge.mcp_http:app --host 0.0.0.0 --port 8080

Then tunnel it with Cloudflare.

In Grok Custom Connector, add the header:
    Authorization: Bearer your-secret-key-here
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Scope, Receive, Send

from forge.tools import create_registry

log = logging.getLogger("forge.mcp_http")

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────

API_KEY = os.getenv("FORGE_MCP_API_KEY")

if not API_KEY:
    log.warning(
        "FORGE_MCP_API_KEY is not set. The MCP server will be accessible without authentication!"
    )


def _is_authorized(scope: Scope) -> bool:
    """Check if the request has a valid Authorization header."""
    if not API_KEY:
        return True  # No key configured = open access (useful for local dev)

    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
    auth_header = headers.get("authorization", "")

    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        return token == API_KEY

    return False


# ──────────────────────────────────────────────────────────────────────────────
# Tool Registration
# ──────────────────────────────────────────────────────────────────────────────

def _register_forge_tools(server: Server) -> None:
    """Register all Forge tools with the MCP server."""
    registry = create_registry()

    for tool in registry.get_raw_tools():
        tool_name = tool["name"]
        tool_description = tool.get("description", "")
        input_schema = tool.get("parameters", {"type": "object", "properties": {}})

        @server.tool(name=tool_name, description=tool_description)
        async def _tool_wrapper(arguments: dict, tool_name=tool_name):
            try:
                result = registry.execute(tool_name, arguments)
                return result
            except Exception as e:
                log.exception("Tool %s failed", tool_name)
                return {"error": str(e)}


# ──────────────────────────────────────────────────────────────────────────────
# MCP Server Setup
# ──────────────────────────────────────────────────────────────────────────────

mcp_server = Server("forge")
_register_forge_tools(mcp_server)

sse_transport = SseServerTransport("/messages")


# ──────────────────────────────────────────────────────────────────────────────
# Authenticated Handlers
# ──────────────────────────────────────────────────────────────────────────────

async def handle_sse(scope: Scope, receive: Receive, send: Send):
    """SSE endpoint (protected)."""
    if not _is_authorized(scope):
        response = JSONResponse({"error": "Unauthorized"}, status_code=401)
        await response(scope, receive, send)
        return

    async with sse_transport.connect_sse(scope, receive, send) as streams:
        await mcp_server.run(
            read_stream=streams[0],
            write_stream=streams[1],
            options=mcp_server.create_initialization_options(),
        )


async def handle_messages(scope: Scope, receive: Receive, send: Send):
    """POST endpoint for client messages (protected)."""
    if not _is_authorized(scope):
        response = JSONResponse({"error": "Unauthorized"}, status_code=401)
        await response(scope, receive, send)
        return

    await sse_transport.handle_post_message(scope, receive, send)


# ──────────────────────────────────────────────────────────────────────────────
# Starlette App
# ──────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app):
    log.info("Forge MCP HTTP server starting (with auth)")
    yield


app = Starlette(
    routes=[
        Route("/mcp", handle_sse, methods=["GET"]),
        Route("/messages", handle_messages, methods=["POST"]),
    ],
    lifespan=lifespan,
)


# ──────────────────────────────────────────────────────────────────────────────
# CLI Runner
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser(description="Forge MCP HTTP Server")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--sandbox", type=str, default="")
    args = parser.parse_args()

    uvicorn.run("forge.mcp_http:app", host=args.host, port=args.port, reload=False)
