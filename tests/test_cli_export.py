"""
tests.test_cli_export
---------------------
Smoke tests for the ``rag-wiki export`` CLI command: argument parsing,
--output flag, and basic wiring. The export logic itself is tested in
``tests/wiki/test_export.py``; these tests verify the CLI integration.

The underlying ``_export_command`` helper is monkey-patched so these tests do
not depend on database connectivity, settings, or the full export pipeline.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from rag_wiki.cli import app

runner = CliRunner()

_Call = dict[str, Path | None]


@pytest.fixture
def mock_export_command(monkeypatch: pytest.MonkeyPatch) -> list[_Call]:
    """Replace ``rag_wiki.cli._export_command`` with a deterministic spy."""
    calls: list[_Call] = []

    async def _fake_export_command(output: Path | None = None) -> None:
        calls.append({"output": output})

    monkeypatch.setattr("rag_wiki.cli._export_command", _fake_export_command)
    return calls


def test_export_help_contains_output_flag() -> None:
    """``rag-wiki export --help`` shows the --output / -o flag."""
    result = runner.invoke(app, ["export", "--help"])
    assert result.exit_code == 0
    assert "--output" in result.stdout or "-o" in result.stdout


def test_export_command_is_wired(
    mock_export_command: list[_Call],
) -> None:
    """``rag-wiki export`` invokes the async helper with no output override."""
    result = runner.invoke(app, ["export"])
    assert result.exit_code == 0
    assert len(mock_export_command) == 1
    assert mock_export_command[0]["output"] is None


def test_export_accepts_output_flag(
    tmp_path: Path,
    mock_export_command: list[_Call],
) -> None:
    """``rag-wiki export --output <path>`` parses and forwards the path."""
    result = runner.invoke(app, ["export", "--output", str(tmp_path)])
    assert result.exit_code == 0
    assert len(mock_export_command) == 1
    assert mock_export_command[0]["output"] == tmp_path


def test_export_with_short_output_flag(
    tmp_path: Path,
    mock_export_command: list[_Call],
) -> None:
    """``rag-wiki export -o <path>`` parses and forwards the path."""
    result = runner.invoke(app, ["export", "-o", str(tmp_path)])
    assert result.exit_code == 0
    assert len(mock_export_command) == 1
    assert mock_export_command[0]["output"] == tmp_path
