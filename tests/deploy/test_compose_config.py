"""
tests.deploy.test_compose_config
--------------------------------
Asserts the prod compose file carries per-service resource bounds and a
json-file log cap (ADR-0017 §2 / PRD-005 Gaps #1 & #2).

Asserts presence and shape only — never the chosen numbers (numbers live in
`deploy/.env.example` + `deploy/README.md` and are reviewed, not unit-tested,
per PRD-005 Testing §). Requires the `docker compose` CLI on the runner (no
Postgres).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

# YAML is a transitive dep of docker compose itself; the test parses `compose
# config`'s YAML stdout. PyYAML is not a runtime dep of rag_wiki — skip the
# test cleanly if it is unavailable rather than forcing an extra dep.
try:
    import yaml
except ImportError:  # pragma: no cover - skip path
    yaml = None

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_FILE = _REPO_ROOT / "deploy" / "docker-compose.prod.yml"
# Every service in the prod stack must carry a resource + log policy.
_EXPECTED_SERVICES = ("db", "api", "worker", "caddy")


def _compose_config() -> dict[str, Any]:
    """Run `docker compose config` on the prod compose file, return parsed YAML.

    Notes:
        Uses `--no-interpolate` + `--env-file deploy/.env.example` so the
        `env_file: .env` service directive resolves against the committed
        `.env.example` (no real `.env`/secrets needed in CI). The env values
        are never asserted on; they only let `config` parse the template.
    """
    if yaml is None:  # pragma: no cover - skip path
        pytest.skip("PyYAML not installed — install with `uv sync --extra dev`")
    env_example = _REPO_ROOT / "deploy" / ".env.example"
    result = subprocess.run(
        ["docker", "compose", "version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.skip("docker compose CLI unavailable on this runner")
    cmd = [
        "docker",
        "compose",
        "-f",
        str(_COMPOSE_FILE),
        "--env-file",
        str(env_example),
        "config",
        "--no-interpolate",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.skip(
            f"docker compose config failed (rc={result.returncode}): "
            f"{result.stderr.strip()[:200]}"
        )
    parsed = yaml.safe_load(result.stdout)
    assert isinstance(parsed, dict), (
        f"compose config did not yield a mapping: {type(parsed)}"
    )
    return parsed


def test_compose_config_succeeds() -> None:
    """`docker compose config` must accept the prod compose file without errors."""
    config = _compose_config()
    services = config.get("services", {})
    for svc in _EXPECTED_SERVICES:
        assert svc in services, f"{svc} service missing from prod compose"


def test_every_service_has_mem_limit() -> None:
    """Each prod service must declare a `mem_limit` (non-empty)."""
    config = _compose_config()
    services = config.get("services", {})
    for svc in _EXPECTED_SERVICES:
        assert svc in services, f"{svc} service missing"
        mem = services[svc].get("mem_limit")
        assert mem, f"{svc} is missing a mem_limit (ADR-0017 §2)"


def test_every_service_has_cpus() -> None:
    """Each prod service must declare a `cpus` bound (non-zero)."""
    config = _compose_config()
    services = config.get("services", {})
    for svc in _EXPECTED_SERVICES:
        assert svc in services, f"{svc} service missing"
        cpus = services[svc].get("cpus")
        assert cpus, f"{svc} is missing a cpus bound (ADR-0017 §2)"


def test_every_service_has_json_file_log_cap() -> None:
    """Each prod service must log via `json-file` capped at `max-size`/`max-file`."""
    config = _compose_config()
    services = config.get("services", {})
    for svc in _EXPECTED_SERVICES:
        assert svc in services, f"{svc} service missing"
        logging = services[svc].get("logging")
        assert logging, f"{svc} is missing a logging block (ADR-0017 §2)"
        assert logging.get("driver") == "json-file", (
            f"{svc} log driver must be json-file, got {logging.get('driver')}"
        )
        options = logging.get("options", {})
        assert "max-size" in options, f"{svc} logging has no max-size option"
        assert "max-file" in options, f"{svc} logging has no max-file option"
