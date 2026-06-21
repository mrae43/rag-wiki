from __future__ import annotations

import logging
import mimetypes
import os

from rag_wiki.exceptions import ParseError
from rag_wiki.ingest.schemas import ParsedChunk

logger = logging.getLogger(__name__)

MIME_DISPATCH: dict[str, str] = {
    "application/pdf": "pdf",
    "text/plain": "simple",
    "text/markdown": "simple",
}


def parse_document(
    file_path: str,
    source_metadata: dict[str, object] | None = None,
) -> list[ParsedChunk]:
    """
    Parse a document into a list of chunks using the appropriate engine.

    Dispatches to the PDF, OCR, simple text, or unstructured parser based on
    MIME type or an explicit ``source_metadata["parser"]`` override.

    Args:
        file_path: Absolute or relative path to the document on disk.
        source_metadata: Optional metadata dict that may contain a ``"parser"``
            key to force a specific engine (``"pdf"``, ``"ocr"``, ``"simple"``,
            ``"unstructured"``).

    Returns:
        A list of ParsedChunk objects. May be empty if the document has no
        extractable content.

    Raises:
        ParseError: If the file does not exist, a required parser dependency is
            missing, or the engine identifier is unknown.
    """
    if not os.path.isfile(file_path):
        raise ParseError(f"File not found: {file_path}")

    metadata = source_metadata or {}
    override = metadata.get("parser")

    if override:
        engine = override
    else:
        mime_type, _ = mimetypes.guess_type(file_path)
        engine = MIME_DISPATCH.get(mime_type or "", "unstructured")

    if not isinstance(engine, str):
        engine = "unstructured"

    if engine == "pdf":
        try:
            from rag_wiki.ingest.parsers.pdf import parse_pdf
        except ImportError:
            raise ParseError("PyMuPDF (fitz) is required for PDF parsing") from None
        return parse_pdf(file_path)

    if engine == "ocr":
        try:
            import fitz

            from rag_wiki.ingest.parsers.pdf import _try_ocr
        except ImportError:
            raise ParseError("PyMuPDF (fitz) is required for OCR parsing") from None
        doc = fitz.open(file_path)
        chunks: list[ParsedChunk] = []
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            text = _try_ocr(page)
            if text.strip():
                from rag_wiki.ingest.chunking import split_by_sections
                from rag_wiki.ingest.schemas import TextChunk

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

    if engine == "simple":
        from rag_wiki.ingest.parsers.simple import parse_simple

        return parse_simple(file_path)

    if engine == "unstructured":
        from rag_wiki.ingest.parsers.unstructured import parse_unstructured

        return parse_unstructured(file_path)

    raise ParseError(f"Unknown parser engine: {engine}")
