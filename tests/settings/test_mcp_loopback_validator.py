"""
tests.settings.test_mcp_loopback_validator
-----------------------------------------
Covers the ADR-0017 §6 / PRD-002 user story 13 contract: the MCP HTTP
transport must refuse non-loopback binds so an unauthenticated MCP HTTP
endpoint cannot be exposed to the tailnet.

Does NOT exercise the stdio transport's host handling beyond confirming it
imposes no constraint — the validator only fires for ``mcp_transport=="http"``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rag_wiki.settings import MCP_LOOPBACK_HOSTS, Settings

# Minimal connection string to satisfy the required ``database_url`` field
# without touching the process-wide settings singleton used elsewhere.
_DATABASE_URL = "postgresql+asyncpg://u:p@db:5432/d"


def _settings(mcp_transport: str, mcp_host: str) -> Settings:
    """Construct a Settings instance with the given MCP transport/host."""
    return Settings(
        database_url=_DATABASE_URL,
        mcp_transport=mcp_transport,
        mcp_host=mcp_host,
    )


# --- HTTP transport: rejects non-loopback binds -------------------------------


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "192.168.1.10", "10.0.0.5", "172.16.0.1", "rag-wiki.tail", "::"],
)
def test_http_rejects_non_loopback_host(host: str) -> None:
    """Non-loopback MCP_HOST is rejected when MCP_TRANSPORT=http."""
    with pytest.raises(ValidationError) as exc_info:
        _settings("http", host)
    assert "loopback" in str(exc_info.value).lower()


def test_http_rejects_wildcard_ipv4() -> None:
    """The all-interfaces wildcard 0.0.0.0 is the dangerous case and is rejected."""
    with pytest.raises(ValidationError):
        _settings("http", "0.0.0.0")


# --- HTTP transport: accepts loopback binds ----------------------------------


@pytest.mark.parametrize("host", sorted(MCP_LOOPBACK_HOSTS))
def test_http_accepts_loopback_host(host: str) -> None:
    """Loopback addresses are accepted when MCP_TRANSPORT=http."""
    settings = _settings("http", host)
    assert settings.mcp_host == host
    assert settings.mcp_transport == "http"


# --- stdio transport: no host constraint -------------------------------------


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "127.0.0.1"])
def test_stdio_imposes_no_host_constraint(host: str) -> None:
    """stdio is a local-trust transport; any MCP_HOST is allowed."""
    settings = _settings("stdio", host)
    assert settings.mcp_host == host
    assert settings.mcp_transport == "stdio"


# --- Defaults: stdio + 127.0.0.1 are valid out of the box --------------------


def test_defaults_are_valid() -> None:
    """The shipped defaults (stdio + 127.0.0.1) must construct cleanly."""
    settings = Settings(database_url=_DATABASE_URL)
    assert settings.mcp_transport == "stdio"
    assert settings.mcp_host == "127.0.0.1"
