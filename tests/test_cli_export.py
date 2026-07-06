"""
tests.test_cli_export
---------------------
Smoke tests for the ``rag-wiki export`` CLI command: argument parsing,
--output flag, and basic wiring. The export logic itself is tested in
``tests/wiki/test_export.py``; these tests verify the CLI integration.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from rag_wiki.cli import app

runner = CliRunner()


def test_export_help_contains_output_flag() -> None:
    """``rag-wiki export --help`` shows the --output / -o flag."""
    result = runner.invoke(app, ["export", "--help"])
    assert result.exit_code == 0
    assert "--output" in result.stdout or "-o" in result.stdout


def test_export_command_is_wired() -> None:
    """``rag-wiki export`` invokes the async helper (success or DB error = wired)."""
    result = runner.invoke(app, ["export"])
    # The command either succeeds (tables exist, 0 pages) or fails with a
    # structured DB error — both mean the wiring is correct. Crucially, it
    # should NOT print Typer's "No such option" or "Missing argument" which
    # would indicate broken argument parsing.
    assert "No such option" not in result.output
    assert "Missing argument" not in result.output


def test_export_accepts_output_flag(tmp_path: Path) -> None:
    """``rag-wiki export --output <path>`` parses without error."""
    result = runner.invoke(app, ["export", "--output", str(tmp_path)])
    # The command will fail at DB connection, but the flag is parsed.
    assert result.exit_code != 0
    # It should not print the typer "Error" for unknown options.
    assert "No such option" not in result.output


def test_export_with_short_output_flag(tmp_path: Path) -> None:
    """``rag-wiki export -o <path>`` parses without error."""
    result = runner.invoke(app, ["export", "-o", str(tmp_path)])
    assert result.exit_code != 0
    assert "No such option" not in result.output
