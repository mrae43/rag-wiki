#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this script as your normal WSL user, not as root." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed or not on PATH." >&2
  exit 1
fi

if [ -d .venv ]; then
  owner="$(stat -c '%U' .venv 2>/dev/null || true)"
  if [ -n "$owner" ] && [ "$owner" != "$(id -un)" ]; then
    echo "The existing .venv is owned by '$owner', not '$(id -un)'."
    if command -v sudo >/dev/null 2>&1; then
      echo "Reclaiming ownership with one sudo prompt..."
      sudo chown -R "$(id -un)":"$(id -gn)" .venv
    else
      echo "Please run: sudo chown -R $(id -un):$(id -gn) .venv" >&2
      exit 1
    fi
  fi
fi

rm -rf .venv
uv venv --python "$(command -v python3)"
uv sync --extra dev

echo "Virtual environment repaired. Future commands should run without sudo."
