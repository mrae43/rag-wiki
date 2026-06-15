from __future__ import annotations

import os

from rag_wiki.ingest.chunking import split_by_sections
from rag_wiki.ingest.schemas import ImageChunk, ParsedChunk, TableChunk, TextChunk


def parse_unstructured(file_path: str) -> list[ParsedChunk]:
    try:
        from unstructured.partition.auto import partition
    except ImportError:
        raise ImportError(
            "unstructured library is required for DOCX/HTML parsing. "
            "Install it with: pip install unstructured"
        ) from None

    elements = partition(filename=file_path)
    file_name = os.path.basename(file_path)
    doc_id_prefix = f"unstructured:{file_path}"

    chunks: list[ParsedChunk] = []
    sections: list[str] = []
    current_section_lines: list[str] = []

    for element in elements:
        elem_type = type(element).__name__
        text = str(getattr(element, "text", "")).strip()

        if not text:
            continue

        if "Title" in elem_type or "Header" in elem_type:
            if current_section_lines:
                sections.append("\n".join(current_section_lines))
                current_section_lines = []
            current_section_lines.append(text)
        elif "Table" in elem_type:
            if current_section_lines:
                sections.append("\n".join(current_section_lines))
                current_section_lines = []
            chunks.append(
                TableChunk(
                    doc_id=f"{doc_id_prefix}:table:{len(chunks)}",
                    text_content=text,
                    source_filename=file_name,
                )
            )
        elif "Image" in elem_type or "Picture" in elem_type:
            if current_section_lines:
                sections.append("\n".join(current_section_lines))
                current_section_lines = []
            image_data = getattr(element, "image_data", None) or getattr(
                element, "bytes", None
            )
            if image_data:
                chunks.append(
                    ImageChunk(
                        doc_id=f"{doc_id_prefix}:image:{len(chunks)}",
                        image_data=image_data,
                        image_mime_type="image/png",
                        source_filename=file_name,
                        caption=text or None,
                    )
                )
        else:
            current_section_lines.append(text)

    if current_section_lines:
        sections.append("\n".join(current_section_lines))

    chunked = split_by_sections([(s, None) for s in sections])
    for idx, (section_text, section_heading, _) in enumerate(chunked):
        chunks.append(
            TextChunk(
                doc_id=f"{doc_id_prefix}:text:{idx}",
                text_content=section_text,
                source_filename=file_name,
                metadata={"section_heading": section_heading or ""},
            )
        )

    return chunks
