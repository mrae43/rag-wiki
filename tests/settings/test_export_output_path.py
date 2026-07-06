"""
tests.settings.test_export_output_path
--------------------------------------
Covers the export_output_path setting: default value, env-var override,
and CLI --output override are not tested here (that's CLI-level).
"""

from __future__ import annotations

from pathlib import Path

from rag_wiki.settings import Settings

_DATABASE_URL = "postgresql+asyncpg://u:p@db:5432/d"


def test_export_output_path_defaults_to_exports() -> None:
    """export_output_path defaults to ./exports."""
    settings = Settings(database_url=_DATABASE_URL)
    assert settings.export_output_path == Path("./exports")


def test_export_output_path_can_be_overridden() -> None:
    """export_output_path accepts an explicit Path value."""
    settings = Settings(
        database_url=_DATABASE_URL,
        export_output_path=Path("/tmp/my-exports"),
    )
    assert settings.export_output_path == Path("/tmp/my-exports")
