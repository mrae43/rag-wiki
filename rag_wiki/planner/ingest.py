"""
rag_wiki.planner.ingest
-----------------------
Rule-based ingest planner that classifies source documents.

The ``IngestPlanner`` class uses MIME type, file density, and optional
metadata overrides to produce a ``SourcePlan`` before any parser runs.
This plan is persisted as a JSONB column on the ``sources`` table and
consumed by ``parse_document()`` in the ingestion pipeline.
"""

from __future__ import annotations

import mimetypes
import os
import uuid

import structlog

from rag_wiki.planner.base import ParserType, PDFParserMode, SourcePlan
from rag_wiki.settings import Settings

logger = structlog.get_logger(__name__)

_MIME_PARSER_MAP: dict[str, ParserType] = {
    "application/pdf": ParserType.PDF,
    "text/plain": ParserType.SIMPLE,
    "text/markdown": ParserType.SIMPLE,
}

_MIME_STRUCTURE_MAP: dict[str, str] = {
    "application/pdf": "structured",
    "text/plain": "semi-structured",
    "text/markdown": "semi-structured",
}

_PARSER_STRUCTURE_MAP: dict[ParserType, str] = {
    ParserType.PDF: "structured",
    ParserType.SIMPLE: "semi-structured",
    ParserType.UNSTRUCTURED: "unstructured",
    ParserType.MINERU: "structured",
}


class IngestPlanner:
    """Rule-based planner that determines how a source should be ingested.

    Classification is purely deterministic — no LLM calls — so confidence
    is always 1.0. The planner checks:

    1. Explicit metadata override (``parser`` key in ``source_metadata``)
    2. MIME type via ``mimetypes.guess_type()``
    3. File density (size vs. configured threshold)

    The fallback parser is always ``SIMPLE`` for rule-based classification.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def create_source_plan(
        self,
        source_id: uuid.UUID,
        file_path: str,
        source_metadata: dict[str, object] | None = None,
        original_filename: str | None = None,
        file_size: int | None = None,
    ) -> SourcePlan:
        """Produce a ``SourcePlan`` for the given source document.

        Args:
            source_id: UUID of the source (must exist or be about to exist).
            file_path: Absolute path to the document on disk, or storage key
                when called from the API (where the original file is in the
                storage provider, not on the local filesystem).
            source_metadata: Optional metadata that may contain a ``"parser"``
                key for explicit parser override.
            original_filename: Original upload filename, used for MIME
                guessing when ``file_path`` has no recognizable extension
                (e.g. UUID-based storage paths).
            file_size: Optional file size in bytes. When provided, the planner
                uses it for density classification instead of calling
                ``os.path.getsize()`` (avoiding a ``FileNotFoundError`` when
                ``file_path`` is an opaque storage key rather than a real path).

        Returns:
            A fully populated ``SourcePlan`` with confidence 1.0.
        """
        metadata = source_metadata or {}
        explicit_override = metadata.get("parser")

        if explicit_override is not None and isinstance(explicit_override, str):
            return self._from_explicit_override(source_id, explicit_override)

        return self._classify_by_mime(
            source_id, file_path, original_filename, file_size
        )

    def _from_explicit_override(
        self,
        source_id: uuid.UUID,
        override: str,
    ) -> SourcePlan:
        if override == "ocr":
            selected_parser = ParserType.PDF
            pdf_mode: PDFParserMode | None = PDFParserMode.WITH_OCR
            detected_type = "pdf"
        else:
            selected_parser = ParserType(override)
            pdf_mode = None
            detected_type = selected_parser.value

        return SourcePlan(
            source_id=source_id,
            detected_type=detected_type,
            detected_structure=_PARSER_STRUCTURE_MAP.get(
                selected_parser, "unstructured"
            ),
            selected_parser=selected_parser,
            pdf_mode=pdf_mode,
            chunking_strategy="section",
            confidence=1.0,
            fallback_parser=ParserType.SIMPLE,
            rationale=f"explicit override in metadata: {override}",
            planner_version=self._settings.planner_version,
        )

    def _classify_by_mime(
        self,
        source_id: uuid.UUID,
        file_path: str,
        original_filename: str | None = None,
        file_size: int | None = None,
    ) -> SourcePlan:
        guess_path = original_filename or file_path
        mime_type, _ = mimetypes.guess_type(guess_path)
        mime_type = mime_type or "application/octet-stream"

        selected_parser = _MIME_PARSER_MAP.get(mime_type, ParserType.UNSTRUCTURED)
        detected_structure = _MIME_STRUCTURE_MAP.get(mime_type, "unstructured")

        pdf_mode: PDFParserMode | None = None
        if selected_parser == ParserType.PDF:
            pdf_mode = PDFParserMode.STANDARD

        if file_size is None:
            file_size = os.path.getsize(file_path)
        density = (
            "large"
            if file_size >= self._settings.planner_density_large_threshold_bytes
            else "small"
        )

        return SourcePlan(
            source_id=source_id,
            detected_type=mime_type,
            detected_structure=detected_structure,
            selected_parser=selected_parser,
            pdf_mode=pdf_mode,
            chunking_strategy="section",
            confidence=1.0,
            fallback_parser=ParserType.SIMPLE,
            rationale=(
                f"mime={mime_type} structure={detected_structure} density={density}"
            ),
            planner_version=self._settings.planner_version,
        )
