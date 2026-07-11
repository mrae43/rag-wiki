#!/usr/bin/env python3
"""
scripts.dump_validator
---------------------
Shared validity checker for Postgres custom-format dumps (``pg_dump -Fc``).

Used by the daily ``backup.sh`` cron and by ``scripts/restore_drill.sh``
(PR-2 / PR-3 of PRD-005). A dump is valid only when it is non-empty *and*
``pg_restore --list`` can read its catalog header — this catches zero-byte
and structurally-corrupt archives before they age into the retention window.

Does NOT connect to a live database; ``pg_restore --list`` only reads the
local archive file. Does NOT modify the dump file.

CLI:
    uv run python scripts/dump_validator.py <dump_file>

Exit codes:
    0 — dump is valid (prints ``OK: <reason>`` to stdout)
    1 — dump is invalid (prints reason to stderr)
    2 — usage error
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


class DumpValidatorError(Exception):
    """Raised when a dump cannot be validated for environmental reasons."""


def validate_dump(dump_path: Path) -> tuple[bool, str]:
    """
    Check whether ``dump_path`` is a restorable Postgres custom-format dump.

    Validation rules:
    1. The path must exist and be a file.
    2. The file must be non-zero bytes.
    3. ``pg_restore --list`` must exit 0, proving the catalog header is intact.

    Args:
        dump_path: Path to the ``*.dump`` file produced by ``pg_dump -Fc``.

    Returns:
        A ``(is_valid, reason)`` tuple. ``reason`` is human-readable and is
        empty when ``is_valid`` is ``True`` only if no extra context is needed
        (here it always contains a short success message).

    Raises:
        DumpValidatorError: if ``pg_restore`` is not on ``PATH``.
    """
    if not dump_path.exists():
        return False, f"dump file does not exist: {dump_path}"
    if not dump_path.is_file():
        return False, f"dump path is not a file: {dump_path}"

    size = dump_path.stat().st_size
    if size == 0:
        return False, "dump file is zero bytes"

    pg_restore = _find_pg_restore()
    if pg_restore is None:
        raise DumpValidatorError("pg_restore not found on PATH")

    logger.info(
        "validating dump",
        dump_path=str(dump_path),
        size_bytes=size,
        pg_restore=pg_restore,
    )

    result = subprocess.run(
        [pg_restore, "--list", str(dump_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        logger.warning(
            "dump failed pg_restore --list validation",
            dump_path=str(dump_path),
            returncode=result.returncode,
            stderr=stderr,
        )
        reason = "pg_restore --list failed"
        if stderr:
            reason += f": {stderr}"
        return False, reason

    logger.info(
        "dump validated successfully",
        dump_path=str(dump_path),
        size_bytes=size,
    )
    return True, f"OK: {size}-byte dump, pg_restore --list succeeded"


def _find_pg_restore() -> str | None:
    """Return the first ``pg_restore`` executable on PATH, or None."""
    # Prefer the one on PATH; on most systems this is enough.
    for candidate in ("pg_restore",):
        try:
            subprocess.run(
                [candidate, "--version"],
                capture_output=True,
                check=False,
            )
            return candidate
        except FileNotFoundError:
            continue
    return None


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <dump_file>", file=sys.stderr)
        return 2

    dump_path = Path(argv[1])
    try:
        is_valid, reason = validate_dump(dump_path)
    except DumpValidatorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if is_valid:
        print(reason)
        return 0

    print(f"invalid dump: {reason}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
