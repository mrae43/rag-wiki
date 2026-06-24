"""Tests for the rule-based ingest planner."""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Generator

import pytest

from rag_wiki.planner.base import ParserType, PDFParserMode
from rag_wiki.planner.ingest import IngestPlanner
from rag_wiki.settings import Settings


@pytest.fixture()
def settings() -> Settings:
    """Return a Settings instance with a small density threshold for testing."""
    return Settings(
        database_url="postgresql+asyncpg://u:p@localhost:5432/test",
        planner_density_large_threshold_bytes=100,
    )


@pytest.fixture()
def planner(settings: Settings) -> IngestPlanner:
    return IngestPlanner(settings)


@pytest.fixture()
def source_id() -> uuid.UUID:
    return uuid.uuid4()


def _write_temp(content: str, suffix: str) -> str:
    with tempfile.NamedTemporaryFile(
        suffix=suffix, mode="w", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        path = f.name
    return path


def _write_bytes(content: bytes, suffix: str) -> str:
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(content)
        path = f.name
    return path


@pytest.fixture()
def pdf_file() -> Generator[str, None, None]:
    path = _write_temp("dummy pdf content", ".pdf")
    yield path
    os.unlink(path)


@pytest.fixture()
def md_file() -> Generator[str, None, None]:
    path = _write_temp("# Heading\n\nContent", ".md")
    yield path
    os.unlink(path)


@pytest.fixture()
def txt_file() -> Generator[str, None, None]:
    path = _write_temp("Plain text content", ".txt")
    yield path
    os.unlink(path)


@pytest.fixture()
def html_file() -> Generator[str, None, None]:
    path = _write_temp("<html><body>Hello</body></html>", ".html")
    yield path
    os.unlink(path)


@pytest.fixture()
def unknown_file() -> Generator[str, None, None]:
    path = _write_temp("some content", ".xyz")
    yield path
    os.unlink(path)


@pytest.fixture()
def large_file() -> Generator[str, None, None]:
    """File that exceeds the test threshold of 100 bytes."""
    path = _write_bytes(b"x" * 200, ".txt")
    yield path
    os.unlink(path)


class TestIngestPlannerMimeClassification:
    """MIME-based dispatch tests."""

    def test_pdf_file_selects_pdf_parser(
        self, planner: IngestPlanner, source_id: uuid.UUID, pdf_file: str
    ) -> None:
        plan = planner.create_source_plan(source_id, pdf_file)
        assert plan.selected_parser == ParserType.PDF

    def test_pdf_file_sets_standard_mode(
        self, planner: IngestPlanner, source_id: uuid.UUID, pdf_file: str
    ) -> None:
        plan = planner.create_source_plan(source_id, pdf_file)
        assert plan.pdf_mode == PDFParserMode.STANDARD

    def test_markdown_file_selects_simple_parser(
        self, planner: IngestPlanner, source_id: uuid.UUID, md_file: str
    ) -> None:
        plan = planner.create_source_plan(source_id, md_file)
        assert plan.selected_parser == ParserType.SIMPLE

    def test_txt_file_selects_simple_parser(
        self, planner: IngestPlanner, source_id: uuid.UUID, txt_file: str
    ) -> None:
        plan = planner.create_source_plan(source_id, txt_file)
        assert plan.selected_parser == ParserType.SIMPLE

    def test_html_file_selects_unstructured_parser(
        self, planner: IngestPlanner, source_id: uuid.UUID, html_file: str
    ) -> None:
        plan = planner.create_source_plan(source_id, html_file)
        assert plan.selected_parser == ParserType.UNSTRUCTURED

    def test_unknown_extension_selects_unstructured(
        self, planner: IngestPlanner, source_id: uuid.UUID, unknown_file: str
    ) -> None:
        plan = planner.create_source_plan(source_id, unknown_file)
        assert plan.selected_parser == ParserType.UNSTRUCTURED


class TestIngestPlannerExplicitOverrides:
    """Explicit parser override tests."""

    def test_explicit_parser_override_in_metadata(
        self, planner: IngestPlanner, source_id: uuid.UUID, pdf_file: str
    ) -> None:
        plan = planner.create_source_plan(
            source_id, pdf_file, source_metadata={"parser": "unstructured"}
        )
        assert plan.selected_parser == ParserType.UNSTRUCTURED
        assert "explicit override" in plan.rationale

    def test_explicit_parser_override_to_simple(
        self, planner: IngestPlanner, source_id: uuid.UUID, pdf_file: str
    ) -> None:
        plan = planner.create_source_plan(
            source_id, pdf_file, source_metadata={"parser": "simple"}
        )
        assert plan.selected_parser == ParserType.SIMPLE

    def test_explicit_parser_override_to_pdf(
        self, planner: IngestPlanner, source_id: uuid.UUID, txt_file: str
    ) -> None:
        plan = planner.create_source_plan(
            source_id, txt_file, source_metadata={"parser": "pdf"}
        )
        assert plan.selected_parser == ParserType.PDF

    def test_explicit_ocr_override_sets_pdf_with_ocr(
        self, planner: IngestPlanner, source_id: uuid.UUID, txt_file: str
    ) -> None:
        plan = planner.create_source_plan(
            source_id, txt_file, source_metadata={"parser": "ocr"}
        )
        assert plan.selected_parser == ParserType.PDF
        assert plan.pdf_mode == PDFParserMode.WITH_OCR


class TestIngestPlannerInvariants:
    """Invariant checks that hold across all classifications."""

    def test_always_assigns_fallback_parser_simple(
        self, planner: IngestPlanner, source_id: uuid.UUID, pdf_file: str
    ) -> None:
        plan = planner.create_source_plan(source_id, pdf_file)
        assert plan.fallback_parser == ParserType.SIMPLE

    def test_confidence_is_always_1(
        self, planner: IngestPlanner, source_id: uuid.UUID, pdf_file: str
    ) -> None:
        plan = planner.create_source_plan(source_id, pdf_file)
        assert plan.confidence == 1.0

    def test_confidence_is_1_with_override(
        self, planner: IngestPlanner, source_id: uuid.UUID, pdf_file: str
    ) -> None:
        plan = planner.create_source_plan(
            source_id, pdf_file, source_metadata={"parser": "unstructured"}
        )
        assert plan.confidence == 1.0

    def test_planner_version_set(
        self, planner: IngestPlanner, source_id: uuid.UUID, txt_file: str
    ) -> None:
        plan = planner.create_source_plan(source_id, txt_file)
        assert plan.planner_version == "1.0.0"

    def test_chunking_strategy_is_section(
        self, planner: IngestPlanner, source_id: uuid.UUID, txt_file: str
    ) -> None:
        plan = planner.create_source_plan(source_id, txt_file)
        assert plan.chunking_strategy == "section"


class TestIngestPlannerDensity:
    """File density classification tests."""

    def test_small_file_density(
        self, planner: IngestPlanner, source_id: uuid.UUID, txt_file: str
    ) -> None:
        plan = planner.create_source_plan(source_id, txt_file)
        assert "density=small" in plan.rationale

    def test_large_file_density(
        self, planner: IngestPlanner, source_id: uuid.UUID, large_file: str
    ) -> None:
        plan = planner.create_source_plan(source_id, large_file)
        assert "density=large" in plan.rationale


class TestIngestPlannerEdgeCases:
    """Edge case tests for the ingest planner."""

    def test_none_metadata(
        self, planner: IngestPlanner, source_id: uuid.UUID, txt_file: str
    ) -> None:
        plan = planner.create_source_plan(source_id, txt_file, source_metadata=None)
        assert plan.selected_parser == ParserType.SIMPLE

    def test_empty_metadata(
        self, planner: IngestPlanner, source_id: uuid.UUID, txt_file: str
    ) -> None:
        plan = planner.create_source_plan(source_id, txt_file, source_metadata={})
        assert plan.selected_parser == ParserType.SIMPLE

    def test_unknown_override_raises_value_error(
        self, planner: IngestPlanner, source_id: uuid.UUID, txt_file: str
    ) -> None:
        with pytest.raises(ValueError):
            planner.create_source_plan(
                source_id, txt_file, source_metadata={"parser": "nonexistent"}
            )
