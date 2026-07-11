"""
tests.scripts.test_dump_validator
----------------------------------
Unit tests for `scripts/dump_validator.py` (PRD-005 Gap #3 / ADR-0017 §7).

Fixtures under ``fixtures/``:
- ``good.dump`` — a real Postgres custom-format dump (``pg_dump -Fc``).
- ``empty.dump`` — zero-byte file.
- ``corrupt.dump`` — non-zero bytes that are not a valid custom-format archive.

Tests that call ``pg_restore --list`` are skipped when the executable is not
on PATH; the CI ``backup-validation`` job always has it available.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from scripts.dump_validator import DumpValidatorError, validate_dump

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VALIDATOR = _REPO_ROOT / "scripts" / "dump_validator.py"
_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_GOOD_DUMP = _FIXTURES / "good.dump"
_EMPTY_DUMP = _FIXTURES / "empty.dump"
_CORRUPT_DUMP = _FIXTURES / "corrupt.dump"


def _pg_restore_available() -> bool:
    """Return True when ``pg_restore`` is on PATH."""
    try:
        subprocess.run(
            ["pg_restore", "--version"],
            capture_output=True,
            check=False,
        )
        return True
    except FileNotFoundError:
        return False


_PG_RESTORE_AVAILABLE = _pg_restore_available()


@pytest.mark.skipif(not _PG_RESTORE_AVAILABLE, reason="pg_restore not on PATH")
def test_validate_dump_good_dump_returns_true() -> None:
    """A valid custom-format dump passes validation with a reason."""
    is_valid, reason = validate_dump(_GOOD_DUMP)
    assert is_valid is True
    assert reason.startswith("OK:")
    assert "pg_restore --list succeeded" in reason


def test_validate_dump_missing_file_returns_false() -> None:
    """A non-existent path returns invalid with a clear reason."""
    missing = _FIXTURES / "does-not-exist.dump"
    is_valid, reason = validate_dump(missing)
    assert is_valid is False
    assert "does not exist" in reason


def test_validate_dump_empty_dump_returns_false_without_pg_restore() -> None:
    """A zero-byte file is rejected before ``pg_restore`` is ever invoked."""
    is_valid, reason = validate_dump(_EMPTY_DUMP)
    assert is_valid is False
    assert "zero bytes" in reason


@pytest.mark.skipif(not _PG_RESTORE_AVAILABLE, reason="pg_restore not on PATH")
def test_validate_dump_corrupt_dump_returns_false() -> None:
    """A non-zero corrupt file fails ``pg_restore --list``."""
    is_valid, reason = validate_dump(_CORRUPT_DUMP)
    assert is_valid is False
    assert "pg_restore --list failed" in reason


def test_validate_dump_raises_when_pg_restore_missing(tmp_path: Path) -> None:
    """A non-zero dump with no ``pg_restore`` on PATH raises an env error."""
    dump = tmp_path / "tiny.dump"
    dump.write_bytes(b"not empty")
    with (
        mock.patch(
            "scripts.dump_validator._find_pg_restore",
            return_value=None,
        ),
        pytest.raises(DumpValidatorError, match="pg_restore not found"),
    ):
        validate_dump(dump)


def test_validate_dump_corrupt_dump_mocked_pg_restore(tmp_path: Path) -> None:
    """A non-zero dump is invalid when ``pg_restore`` returns non-zero."""
    dump = tmp_path / "tiny.dump"
    dump.write_bytes(b"not empty")
    with mock.patch(
        "scripts.dump_validator.subprocess.run",
        return_value=mock.Mock(returncode=1, stderr="bad header"),
    ):
        is_valid, reason = validate_dump(dump)
    assert is_valid is False
    assert "pg_restore --list failed" in reason
    assert "bad header" in reason


@pytest.mark.skipif(not _PG_RESTORE_AVAILABLE, reason="pg_restore not on PATH")
def test_cli_exits_zero_on_good_dump() -> None:
    """The CLI prints OK and exits 0 for a valid dump."""
    result = subprocess.run(
        [sys.executable, str(_VALIDATOR), str(_GOOD_DUMP)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.startswith("OK:")
    assert "pg_restore --list succeeded" in result.stdout


def test_cli_exits_nonzero_on_empty_dump() -> None:
    """The CLI exits 1 and logs a reason for a zero-byte dump."""
    result = subprocess.run(
        [sys.executable, str(_VALIDATOR), str(_EMPTY_DUMP)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "zero bytes" in result.stderr


@pytest.mark.skipif(not _PG_RESTORE_AVAILABLE, reason="pg_restore not on PATH")
def test_cli_exits_nonzero_on_corrupt_dump() -> None:
    """The CLI exits 1 and logs a reason for a corrupt dump."""
    result = subprocess.run(
        [sys.executable, str(_VALIDATOR), str(_CORRUPT_DUMP)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "pg_restore --list failed" in result.stderr


def test_cli_exits_nonzero_on_missing_dump() -> None:
    """The CLI exits 1 for a missing dump file."""
    result = subprocess.run(
        [sys.executable, str(_VALIDATOR), str(_FIXTURES / "missing.dump")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "does not exist" in result.stderr


def test_cli_exits_nonzero_on_usage_error() -> None:
    """The CLI exits 2 when called without a dump argument."""
    result = subprocess.run(
        [sys.executable, str(_VALIDATOR)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "usage:" in result.stderr
