#!/usr/bin/env python3
"""
scripts.pin_image_tag
--------------------
Idempotent image-tag pinner invoked by the `deploy` CI job over SSH before
`docker compose pull` (ADR-0017 §4 / PRD-005 Gap #5).

Atomically sets the `IMAGE_TAG=` line in the VM's `.env` to a SHA-derived tag
(`sha-<short>`), leaving every other line byte-for-byte unchanged and
preserving the line's relative position so diffs stay readable. Refuses to
invent the `IMAGE_TAG=` line if absent (protects operators who copy
`.env.example` to `.env` but forget to set `IMAGE_TAG`).

Does NOT touch `settings.py`, does NOT pass `IMAGE_TAG` inline to compose
(matches ADR-0017 §4 verbatim — the tag persists in `.env` so subsequent
manual `up -d` calls keep that tag until an explicit rollback).

CLI:
    uv run python scripts/pin_image_tag.py <env_path> <tag>

Exit codes:
    0 — pinned (or already equal — idempotent no-op)
    1 — `IMAGE_TAG=` line absent in the env file
    2 — read/write I/O error
"""

from __future__ import annotations

import sys
from pathlib import Path

_KEY = "IMAGE_TAG"


def pin_image_tag(env_path: Path, tag: str) -> None:
    """
    Set the `IMAGE_TAG=` line in ``env_path`` to ``tag``, leaving every other
    line byte-identical.

    Idempotent: a no-op when the line already carries ``tag``. Preserves the
    line's position in the file.

    Args:
        env_path: Path to the VM's `.env` file (must already exist with an
            `IMAGE_TAG=` line).
        tag: The SHA-derived tag to pin, e.g. `sha-a1b2c3d`.

    Raises:
        KeyError: if no `IMAGE_TAG=` line exists in ``env_path`` (the pinner
            never invents the line — PRD-005 §"Further Notes").
        OSError: if the file cannot be read or written.
    """
    original = env_path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    found = False
    new_lines: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(f"{_KEY}=") or stripped == _KEY:
            new_lines.append(f"{_KEY}={tag}\n")
            found = True
        else:
            new_lines.append(line)
    if not found:
        raise KeyError(
            f"{_KEY}= not found in {env_path} — the pinner refuses to invent "
            f"the line. Add `IMAGE_TAG=` to the file before re-running."
        )
    new_text = "".join(new_lines)
    if new_text == original:
        return  # idempotent no-op
    env_path.write_text(new_text, encoding="utf-8")


def _main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <env_path> <tag>", file=sys.stderr)
        return 2
    env_path = Path(argv[1])
    tag = argv[2]
    try:
        pin_image_tag(env_path, tag)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"pinned {_KEY}={tag} in {env_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
