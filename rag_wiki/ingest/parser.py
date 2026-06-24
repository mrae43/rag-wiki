from __future__ import annotations

import logging
import os

from rag_wiki.exceptions import ParseError
from rag_wiki.ingest.schemas import ParsedChunk
from rag_wiki.planner.base import ParserType, PDFParserMode, SourcePlan

logger = logging.getLogger(__name__)


def parse_document(
    file_path: str,
    source_plan: SourcePlan,
) -> list[ParsedChunk]:
    """Parse a document into a list of chunks using the plan's selected parser.

    Args:
        file_path: Absolute or relative path to the document on disk.
        source_plan: The ingest plan specifying which parser to use and how.

    Returns:
        A list of ParsedChunk objects. May be empty if the document has no
        extractable content.

    Raises:
        ParseError: If the file does not exist, a required parser dependency is
            missing, or the engine identifier is unknown.
    """
    if not os.path.isfile(file_path):
        raise ParseError(f"File not found: {file_path}")

    if source_plan.selected_parser == ParserType.PDF:
        if source_plan.pdf_mode == PDFParserMode.WITH_OCR:
            return _parse_pdf_ocr(file_path)
        try:
            from rag_wiki.ingest.parsers.pdf import parse_pdf
        except ImportError:
            raise ParseError("PyMuPDF (fitz) is required for PDF parsing") from None
        return parse_pdf(file_path)

    if source_plan.selected_parser == ParserType.SIMPLE:
        from rag_wiki.ingest.parsers.simple import parse_simple

        return parse_simple(file_path)

    if source_plan.selected_parser == ParserType.UNSTRUCTURED:
        from rag_wiki.ingest.parsers.unstructured import parse_unstructured

        return parse_unstructured(file_path)

    raise ParseError(f"Unknown parser engine: {source_plan.selected_parser}")


def _parse_pdf_ocr(file_path: str) -> list[ParsedChunk]:
    """Parse a PDF using OCR per page (scanned/image-based PDFs only)."""
    try:
        import fitz

        from rag_wiki.ingest.parsers.pdf import _try_ocr
    except ImportError:
        raise ParseError("PyMuPDF (fitz) is required for OCR parsing") from None

    from rag_wiki.ingest.chunking import split_by_sections
    from rag_wiki.ingest.schemas import TextChunk

    doc = fitz.open(file_path)
    chunks: list[ParsedChunk] = []
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        text = _try_ocr(page)
        if text.strip():
            chunked = split_by_sections([(text.strip(), page_num)])
            for idx, (section_text, _, _) in enumerate(chunked):
                chunks.append(
                    TextChunk(
                        doc_id=f"ocr:{file_path}:{page_num}:{idx}",
                        text_content=section_text,
                        page_number=page_num,
                        source_filename=os.path.basename(file_path),
                    )
                )
        page = None
    doc.close()
    return chunks
