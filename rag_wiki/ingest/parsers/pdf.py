from __future__ import annotations

import os
from collections.abc import Generator

import fitz

from rag_wiki.ingest.chunking import MAX_CHARS, OVERLAP_CHARS, split_by_sections
from rag_wiki.ingest.schemas import ChunkType, ImageChunk, ParsedChunk, TableChunk, TextChunk

OCR_TEXT_THRESHOLD = 50


def _detect_body_font_size(page: fitz.Page) -> float:
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE).get("blocks", [])
    sizes: list[float] = []
    for block in blocks:
        if block.get("type") == 0:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("size", 0) > 0:
                        sizes.append(span["size"])
    if not sizes:
        return 11.0
    sorted_sizes = sorted(sizes)
    mid = len(sorted_sizes) // 2
    return sorted_sizes[mid]


def _page_heading_sections(page: fitz.Page, body_size: float) -> list[str]:
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE).get("blocks", [])
    sections: list[str] = []
    current_lines: list[str] = []

    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            max_span_size = max(
                (s.get("size", 0) for s in line.get("spans", [])),
                default=0,
            )
            text = "".join(s.get("text", "") for s in line.get("spans", []))

            if max_span_size >= body_size * 1.5 and text.strip():
                if current_lines:
                    sections.append("\n".join(current_lines))
                    current_lines = []
                current_lines.append(text.strip())
            else:
                current_lines.append(text.strip())

    if current_lines:
        sections.append("\n".join(current_lines))

    return sections


def _extract_table_chunks(page: fitz.Page, page_num: int, file_name: str, doc_id_prefix: str) -> list[TableChunk]:
    tables = page.find_tables()
    chunks: list[TableChunk] = []
    for idx, table in enumerate(tables):
        df = table.to_pandas()
        headers = list(df.columns) if df.columns.tolist() else []
        md = table.to_markdown()
        chunks.append(
            TableChunk(
                doc_id=f"{doc_id_prefix}:table:{page_num}:{idx}",
                text_content=md,
                headers=headers,
                page_number=page_num,
                source_filename=file_name,
            )
        )
    return chunks


def _extract_image_chunks(page: fitz.Page, page_num: int, file_name: str, doc_id_prefix: str) -> list[ImageChunk]:
    chunks: list[ImageChunk] = []
    for idx, img_info in enumerate(page.get_images(full=True)):
        xref = img_info[0]
        base_image = page.parent.extract_image(xref)
        if base_image is None:
            continue
        image_bytes = base_image["image"]
        mime_type = base_image.get("ext", "image/png")
        if mime_type == "jpeg":
            mime_type = "image/jpeg"
        elif mime_type == "png":
            mime_type = "image/png"
        elif mime_type == "gif":
            mime_type = "image/gif"
        else:
            mime_type = f"image/{mime_type}"
        chunks.append(
            ImageChunk(
                doc_id=f"{doc_id_prefix}:image:{page_num}:{idx}",
                image_data=image_bytes,
                image_mime_type=mime_type,
                page_number=page_num,
                source_filename=file_name,
            )
        )
    return chunks


def _try_ocr(page: fitz.Page) -> str:
    try:
        tp = page.get_textpage_ocr(flags=fitz.TEXT_PRESERVE_WHITESPACE)
        text: str = page.get_text(textpage=tp)
        return text
    except Exception:
        return ""


def parse_pdf(file_path: str) -> list[ParsedChunk]:
    doc = fitz.open(file_path)
    file_name = os.path.basename(file_path)
    doc_id_prefix = f"pdf:{file_path}"

    chunks: list[ParsedChunk] = []
    full_text_pages: list[str] = []

    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        body_size = _detect_body_font_size(page)

        text = page.get_text("text").strip()

        used_ocr = False
        if len(text) < OCR_TEXT_THRESHOLD:
            ocr_text = _try_ocr(page)
            if ocr_text and len(ocr_text.strip()) >= OCR_TEXT_THRESHOLD:
                text = ocr_text.strip()
                used_ocr = True

        image_chunks = _extract_image_chunks(page, page_num, file_name, doc_id_prefix)
        chunks.extend(image_chunks)

        if not used_ocr:
            table_chunks = _extract_table_chunks(page, page_num, file_name, doc_id_prefix)
            chunks.extend(table_chunks)

        full_text_pages.append(text or "")

    if any(t.strip() for t in full_text_pages):
        all_sections: list[str] = []
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            body_size = _detect_body_font_size(page)
            sections = _page_heading_sections(page, body_size)
            if not sections:
                full = full_text_pages[page_num]
                if full.strip():
                    all_sections.append(full)
            else:
                all_sections.extend(sections)

        chunked = split_by_sections(all_sections)
        for idx, (section_text, section_heading) in enumerate(chunked):
            chunks.append(
                TextChunk(
                    doc_id=f"{doc_id_prefix}:text:{idx}",
                    text_content=section_text,
                    source_filename=file_name,
                    metadata={"section_heading": section_heading or ""},
                )
            )

    doc.close()
    return chunks
