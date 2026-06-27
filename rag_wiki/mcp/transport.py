"""
rag_wiki.mcp.transport
----------------------
MCP server transport layer — entrypoint for running the MCP server.

Configures structlog for stderr output, creates the server via
create_mcp_server(), and dispatches to the selected transport (stdio or HTTP).
"""

from __future__ import annotations

import sys
from typing import Literal

import structlog
from pydantic import AnyHttpUrl

from rag_wiki.mcp.server import create_mcp_server
from rag_wiki.settings import get_settings


def _configure_structlog() -> None:
    """Configure structlog to write log messages to stderr as plain text."""
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def run(
    transport: Literal["stdio", "http"] | None = None,
    host: str | None = None,
    port: int | None = None,
    api_url: str | None = None,
) -> None:
    """Start the MCP server.

    Configures structlog, creates the server, and dispatches to the
    selected transport. Parameters override settings/env vars when set;
    ``None`` means use the value from settings.

    Args:
        transport: Transport to use (``"stdio"`` or ``"http"``).
        host: Bind host for HTTP transport.
        port: Bind port for HTTP transport.
        api_url: Backend API URL for tool proxying.

    Raises:
        ValueError: If HTTP transport is selected and no port is provided.
    """
    _configure_structlog()

    settings = get_settings()
    _transport = transport if transport is not None else settings.mcp_transport
    _host = host if host is not None else settings.mcp_host
    _port = port if port is not None else settings.mcp_port
    if api_url is not None:
        settings.mcp_api_url = AnyHttpUrl(api_url)

    server = create_mcp_server(settings=settings)
    if _transport == "http":
        if _port is None:
            raise ValueError("MCP HTTP transport requires a port")
        server.run(transport="http", host=_host, port=_port)
    else:
        server.run(transport="stdio")
