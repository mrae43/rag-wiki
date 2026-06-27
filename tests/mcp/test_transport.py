"""
tests.mcp.test_transport
------------------------
Tests for the MCP transport layer (run() entrypoint, structlog config,
transport dispatch).

Uses mocked create_mcp_server and FastMCP.run to avoid actually starting
a long-lived server process.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastmcp import FastMCP

from rag_wiki.mcp.transport import run


def _settings(**overrides: str | int | None) -> SimpleNamespace:
    """Return a minimal settings-like object with MCP defaults."""
    defaults: dict[str, str | int | None] = {
        "mcp_transport": "stdio",
        "mcp_host": "127.0.0.1",
        "mcp_port": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestRunStdio:
    """Tests for stdio transport dispatch."""

    def test_dispatches_stdio(self) -> None:
        settings = _settings(mcp_transport="stdio")
        with (
            patch("rag_wiki.mcp.transport.get_settings", return_value=settings),
            patch("rag_wiki.mcp.transport.create_mcp_server") as mock_create,
            patch("fastmcp.FastMCP.run") as mock_run,
        ):
            mock_create.return_value = FastMCP(name="test")
            run(transport="stdio")
            mock_run.assert_called_once_with(transport="stdio")
            mock_create.assert_called_once()

    def test_defaults_to_stdio(self) -> None:
        settings = _settings(mcp_transport="stdio")
        with (
            patch("rag_wiki.mcp.transport.get_settings", return_value=settings),
            patch("rag_wiki.mcp.transport.create_mcp_server") as mock_create,
            patch("fastmcp.FastMCP.run") as mock_run,
        ):
            mock_create.return_value = FastMCP(name="test")
            run()
            mock_run.assert_called_once_with(transport="stdio")

    def test_overrides_transport(self) -> None:
        settings = _settings(mcp_transport="stdio")
        with (
            patch("rag_wiki.mcp.transport.get_settings", return_value=settings),
            patch("rag_wiki.mcp.transport.create_mcp_server") as mock_create,
            patch("fastmcp.FastMCP.run") as mock_run,
        ):
            mock_create.return_value = FastMCP(name="test")
            run(transport="http", host="0.0.0.0", port=8080)
            mock_run.assert_called_once_with(
                transport="http", host="0.0.0.0", port=8080
            )


class TestRunHttp:
    """Tests for HTTP transport dispatch."""

    def test_dispatches_http(self) -> None:
        settings = _settings(mcp_transport="http", mcp_host="0.0.0.0", mcp_port=8080)
        with (
            patch("rag_wiki.mcp.transport.get_settings", return_value=settings),
            patch("rag_wiki.mcp.transport.create_mcp_server") as mock_create,
            patch("fastmcp.FastMCP.run") as mock_run,
        ):
            mock_create.return_value = FastMCP(name="test")
            run(transport="http", host="0.0.0.0", port=8080)
            mock_run.assert_called_once_with(
                transport="http", host="0.0.0.0", port=8080
            )

    def test_missing_port_raises_value_error(self) -> None:
        settings = _settings(mcp_transport="http", mcp_port=None)
        with (
            patch("rag_wiki.mcp.transport.get_settings", return_value=settings),
            patch("rag_wiki.mcp.transport.create_mcp_server"),
            pytest.raises(ValueError, match="requires a port"),
        ):
            run(transport="http", port=None)

    def test_missing_port_from_settings_raises(self) -> None:
        settings = _settings(mcp_transport="http", mcp_port=None)
        with (
            patch("rag_wiki.mcp.transport.get_settings", return_value=settings),
            patch("rag_wiki.mcp.transport.create_mcp_server"),
            pytest.raises(ValueError, match="requires a port"),
        ):
            run(transport="http")

    def test_default_port_from_settings(self) -> None:
        settings = _settings(mcp_transport="http", mcp_port=9090)
        with (
            patch("rag_wiki.mcp.transport.get_settings", return_value=settings),
            patch("rag_wiki.mcp.transport.create_mcp_server") as mock_create,
            patch("fastmcp.FastMCP.run") as mock_run,
        ):
            mock_create.return_value = FastMCP(name="test")
            run(transport="http", host="0.0.0.0")
            mock_run.assert_called_once_with(
                transport="http", host="0.0.0.0", port=9090
            )


class TestStructlogConfig:
    """Tests for structlog configuration."""

    def test_structlog_configured_on_run(self) -> None:
        settings = _settings()
        with (
            patch("rag_wiki.mcp.transport.get_settings", return_value=settings),
            patch("rag_wiki.mcp.transport.create_mcp_server") as mock_create,
            patch("fastmcp.FastMCP.run"),
            patch("structlog.configure") as mock_configure,
        ):
            mock_create.return_value = FastMCP(name="test")
            run(transport="stdio")
            mock_configure.assert_called_once()
