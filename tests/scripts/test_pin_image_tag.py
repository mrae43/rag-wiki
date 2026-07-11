"""
tests.scripts.test_pin_image_tag
-------------------------------
Unit tests for `scripts/pin_image_tag.py` (PRD-005 Gap #5 / ADR-0017 §4).

Asserts the diff shape per PRD-005 Testing §"Image-tag pinner":
- exactly one `IMAGE_TAG=` line set to the new tag
- all other lines byte-identical
- idempotent on repeat
- non-zero exit when no `IMAGE_TAG=` line exists
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.pin_image_tag import pin_image_tag

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PINNER = _REPO_ROOT / "scripts" / "pin_image_tag.py"

_SAMPLE_ENV = """\
# deploy env
GHCR_OWNER=acme
IMAGE_TAG=sha-oldvalue
POSTGRES_PASSWORD=secret
# trailing comment
LLM_API_KEY=sk-xxx
"""


def _write_env(tmp_path: Path, content: str) -> Path:
    """Write the given `.env` content to a temp path and return it."""
    env_path = tmp_path / ".env"
    env_path.write_text(content, encoding="utf-8")
    return env_path


def test_pinner_updates_only_the_image_tag_line(tmp_path: Path) -> None:
    """Pin the tag; exactly one `IMAGE_TAG=` line changes, rest byte-identical."""
    env_path = _write_env(tmp_path, _SAMPLE_ENV)
    pin_image_tag(env_path, "sha-deadbee")
    result = env_path.read_text(encoding="utf-8")
    assert "IMAGE_TAG=sha-deadbee\n" in result
    assert "IMAGE_TAG=sha-oldvalue" not in result
    # Keep exactly one IMAGE_TAG= line.
    assert result.count("IMAGE_TAG=") == 1
    # All non-IMAGE_TAG lines remain byte-identical.
    expected_other = [
        "# deploy env\n",
        "GHCR_OWNER=acme\n",
        "POSTGRES_PASSWORD=secret\n",
        "# trailing comment\n",
        "LLM_API_KEY=sk-xxx\n",
    ]
    other_lines = [
        ln for ln in result.splitlines(keepends=True) if not ln.startswith("IMAGE_TAG=")
    ]
    assert other_lines == expected_other


def test_pinner_preserves_line_position(tmp_path: Path) -> None:
    """The `IMAGE_TAG=` line stays where it was in the original file."""
    env_path = _write_env(tmp_path, _SAMPLE_ENV)
    pin_image_tag(env_path, "sha-abcdef0")
    lines = env_path.read_text(encoding="utf-8").splitlines()
    # IMAGE_TAG was the 3rd line in _SAMPLE_ENV (0-indexed line 2):
    # "# deploy env", "GHCR_OWNER=acme", "IMAGE_TAG=...".
    assert lines[2].startswith("IMAGE_TAG=sha-abcdef0")


def test_pinner_is_idempotent_for_same_tag(tmp_path: Path) -> None:
    """Pinning the same tag twice is a no-op (file content byte-identical)."""
    env_path = _write_env(tmp_path, _SAMPLE_ENV)
    pin_image_tag(env_path, "sha-1111111")
    first = env_path.read_text(encoding="utf-8")
    pin_image_tag(env_path, "sha-1111111")
    second = env_path.read_text(encoding="utf-8")
    assert first == second


def test_pinner_updates_to_a_different_tag(tmp_path: Path) -> None:
    """Pinning a different tag updates only the value."""
    env_path = _write_env(tmp_path, _SAMPLE_ENV)
    pin_image_tag(env_path, "sha-first")
    pin_image_tag(env_path, "sha-second")
    result = env_path.read_text(encoding="utf-8")
    assert "IMAGE_TAG=sha-second" in result
    assert "IMAGE_TAG=sha-first" not in result
    assert result.count("IMAGE_TAG=") == 1


def test_pinner_errors_when_image_tag_line_absent(tmp_path: Path) -> None:
    """Pinning refuses to invent an `IMAGE_TAG=` line that wasn't there."""
    env_path = _write_env(tmp_path, "GHCR_OWNER=acme\nPOSTGRES_PASSWORD=secret\n")
    with pytest.raises(KeyError, match="IMAGE_TAG"):
        pin_image_tag(env_path, "sha-deadbee")


def test_pinner_cli_exits_nonzero_when_image_tag_absent(tmp_path: Path) -> None:
    """The CLI entrypoint exits 1 when `IMAGE_TAG=` is missing."""
    env_path = _write_env(tmp_path, "GHCR_OWNER=acme\n")
    result = subprocess.run(
        [sys.executable, str(_PINNER), str(env_path), "sha-deadbee"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "IMAGE_TAG" in result.stderr


def test_pinner_cli_exits_zero_and_pins(tmp_path: Path) -> None:
    """The CLI entrypoint exits 0 and writes the new tag."""
    env_path = _write_env(tmp_path, _SAMPLE_ENV)
    result = subprocess.run(
        [sys.executable, str(_PINNER), str(env_path), "sha-abc1234"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "IMAGE_TAG=sha-abc1234" in env_path.read_text(encoding="utf-8")


def test_pinner_cli_idempotent_repeat(tmp_path: Path) -> None:
    """Calling the CLI twice with the same tag is a no-op on the second call."""
    env_path = _write_env(tmp_path, _SAMPLE_ENV)
    first = subprocess.run(
        [sys.executable, str(_PINNER), str(env_path), "sha-xyz"],
        capture_output=True,
        text=True,
    )
    assert first.returncode == 0
    content_after_first = env_path.read_text(encoding="utf-8")
    second = subprocess.run(
        [sys.executable, str(_PINNER), str(env_path), "sha-xyz"],
        capture_output=True,
        text=True,
    )
    assert second.returncode == 0
    assert env_path.read_text(encoding="utf-8") == content_after_first
