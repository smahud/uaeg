"""ASGI application assembly for the public MCP listener (PRD FR-004, FR-005).

- FastMCP (official MCP SDK) provides the standards-compatible Streamable
  HTTP transport at ``/mcp`` with JSON responses and stateful sessions.
- ``/healthz`` and ``/`` are public, unauthenticated service info routes.
- ``BearerAuthMiddleware`` guards exactly the ``/mcp`` route.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .auth import BearerAuthMiddleware
from .mcp_tools import MCP_INSTRUCTIONS, register_tools
from .runtime import Runtime


def build_mcp_asgi_app(runtime: Runtime):
    mcp = FastMCP(
        name="UAEG",
        instructions=MCP_INSTRUCTIONS,
        json_response=True,          # plain application/json responses
        stateless_http=False,        # stateful sessions via Mcp-Session-Id
        streamable_http_path="/mcp",
        host=runtime.config.mcp.bind,
        port=runtime.config.mcp.port,
    )
    register_tools(mcp, runtime)
    # Report UAEG's own version in the MCP initialize result.
    mcp._mcp_server.version = __version__

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": "UAEG",
                "version": __version__,
                "uptime_seconds": round(runtime.uptime_seconds, 1),
            }
        )

    @mcp.custom_route("/", methods=["GET"])
    async def root(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "service": "UAEG",
                "version": __version__,
                "description": "Universal Agent Execution Gateway",
                "mcp_endpoint": runtime.mcp_url(),
                "health": "/healthz",
                "docs": "https://github.com/smahud/uaeg",
                "note": "Use an MCP client with a bearer agent key to access /mcp",
            }
        )

    app = mcp.streamable_http_app()
    return BearerAuthMiddleware(app, runtime.store)
