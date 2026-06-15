from __future__ import annotations

import os
import re

from rag_wiki.ingest.chunking import split_by_sections
from rag_wiki.ingest.schemas import ParsedChunk, TextChunk


def _detect_sections_txt(text: str) -> list[str]:
    lines = text.strip().split("\n")
    sections: list[str] = []
    current: list[str] = []
    blank_count = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            blank_count += 1
            if blank_count >= 2 and current:
                sections.append("\n".join(current))
                current = []
            continue
        blank_count = 0

        is_heading = (
            stripped.isupper() and len(stripped) > 3 and len(stripped) < 100
        ) or stripped.endswith(":")

        if is_heading and current:
            sections.append("\n".join(current))
            current = [stripped]
        else:
            current.append(stripped)

    if current:
        sections.append("\n".join(current))

    return sections if sections else [text.strip()]


def _detect_sections_md(text: str) -> list[str]:
    lines = text.strip().split("\n")
    sections: list[str] = []
    current: list[str] = []

    for line in lines:
        if re.match(r"^#{1,6}\s", line.strip()):
            if current:
                sections.append("\n".join(current))
            current = [line]
        else:
            current.append(line)

    if current:
        sections.append("\n".join(current))

    return sections if sections else [text.strip()]


def parse_simple(file_path: str) -> list[ParsedChunk]:
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".md", ".markdown", ".mdown"):
        raw_sections = _detect_sections_md(text)
    else:
        raw_sections = _detect_sections_txt(text)

    chunked = split_by_sections(raw_sections)
    chunks: list[ParsedChunk] = []
    for idx, (section_text, section_heading) in enumerate(chunked):
        chunks.append(
            TextChunk(
                doc_id=f"simple:{file_path}:{idx}",
                text_content=section_text,
                source_filename=os.path.basename(file_path),
                metadata={"section_heading": section_heading or ""},
            )
        )
    return chunks
