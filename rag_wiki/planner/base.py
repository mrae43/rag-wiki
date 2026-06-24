"""
rag_wiki.planner.base
---------------------
Canonical enums and Pydantic data models for the planner subsystem.

These models define the plan contracts that cross the boundary between
the planner and the rest of the system (ingest, retrieval, API). They
are NOT SQLAlchemy ORM models.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel


class ParserType(StrEnum):
    """Canonical parser identifiers.

    Each value maps to a parser module in ``rag_wiki.ingest.parsers.*``.
    ``MINERU`` is declared but not yet implemented (see ADR-0011).
    """

    PDF = "pdf"
    SIMPLE = "simple"
    UNSTRUCTURED = "unstructured"
    MINERU = "mineru"


class PDFParserMode(StrEnum):
    """PDF-specific parsing mode.

    ``STANDARD`` uses PyMuPDF's built-in text extraction. ``WITH_OCR``
    applies OCR on top for scanned/image-based PDFs.
    """

    STANDARD = "standard"
    WITH_OCR = "with_ocr"


class QueryType(StrEnum):
    """Query classification taxonomy for v1.

    Each type maps to a retrieval strategy in ``rag_wiki.retrieval``.
    Navigational queries are excluded from v1 (see ADR-0014).
    """

    FACTUAL_LOOKUP = "factual_lookup"
    RELATIONSHIP_QUERY = "relationship_query"
    SUMMARIZATION = "summarization"
    COMPARISON = "comparison"


class SourcePlan(BaseModel):
    """Plan for how a single source document should be ingested.

    Produced by the ingest planner before any parser is invoked. Stored
    as a JSONB column on the ``sources`` table.
    """

    source_id: UUID
    detected_type: str
    detected_structure: str
    selected_parser: ParserType
    pdf_mode: PDFParserMode | None = None
    chunking_strategy: str
    confidence: float
    fallback_parser: ParserType = ParserType.SIMPLE
    rationale: str
    planner_version: str


class QueryPlan(BaseModel):
    """Plan for how a query should be processed.

    Produced by the query planner before retrieval begins. Persisted in
    the ``query_plans`` table.
    """

    query_id: UUID
    raw_query: str
    classified_type: QueryType
    retrieval_depth: str
    seed_count: int
    termination_condition: str
    confidence: float
    classification_source: str
    model_used: str | None = None
    rationale: str
    planner_version: str
