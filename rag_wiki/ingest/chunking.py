from __future__ import annotations
from collections.abc import Sequence

MAX_TOKENS = 512
OVERLAP_TOKENS = 64
CHARS_PER_TOKEN = 4
MAX_CHARS = MAX_TOKENS * CHARS_PER_TOKEN  # 2048
OVERLAP_CHARS = OVERLAP_TOKENS * CHARS_PER_TOKEN  # 256


def count_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def _split_paragraph(text: str) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return paragraphs if paragraphs else [text]

def split_by_sections(
    sections: Sequence[tuple[str, int | None]],
    max_chars: int = MAX_CHARS,
    overlap_chars: int = OVERLAP_CHARS,
) -> list[tuple[str, str | None, int | None]]:
    result: list[tuple[str, str | None, int | None]] = []
    if not sections:
        return result

    buffer = ""
    buffer_section: str | None = None
    buffer_page: int | None = None

    for section, page_num in sections:
        if len(section) > max_chars:
            if buffer:
                result.append((buffer, buffer_section, buffer_page))
                buffer = ""
                buffer_section = None
                buffer_page = None
            paragraphs = _split_paragraph(section)
            for para in paragraphs:
                if len(para) <= max_chars:
                    if buffer:
                        result.append((buffer, buffer_section, buffer_page))
                    buffer = para
                    buffer_section = para.split("\n")[0]
                    buffer_page = page_num
                else:
                    if buffer:
                        result.append((buffer, buffer_section, buffer_page))
                    for i in range(0, len(para), max_chars - overlap_chars):
                        chunk = para[i : i + max_chars]
                        if chunk:
                            heading = (
                                para[: min(80, len(para) // 4)] if i == 0 else None
                            )
                            result.append((chunk, heading, page_num))
                    buffer = ""
                    buffer_section = None
                    buffer_page = None
            continue

        if len(buffer) + len(section) + 2 <= max_chars:
            if buffer:
                buffer += "\n\n" + section
                if buffer_page != page_num:
                    buffer_page = None
            else:
                buffer = section
                buffer_section = section.split("\n")[0]
                buffer_page = page_num
            continue

        if buffer:
            result.append((buffer, buffer_section, buffer_page))
        buffer = section
        buffer_section = section.split("\n")[0]
        buffer_page = page_num

    if buffer:
        result.append((buffer, buffer_section, buffer_page))

    result = _apply_overlap(result, overlap_chars)
    return result


def _apply_overlap(
    chunks: list[tuple[str, str | None, int | None]],
    overlap_chars: int,
) -> list[tuple[str, str | None, int | None]]:
    if len(chunks) <= 1:
        return chunks

    result: list[tuple[str, str | None, int | None]] = [chunks[0]]
    for i in range(1, len(chunks)):
        prev_text = chunks[i - 1][0]
        curr_text, heading, page_num = chunks[i]
        tail = (
            prev_text[-overlap_chars:] if len(prev_text) > overlap_chars else prev_text
        )
        overlapped = tail + curr_text
        result.append((overlapped, heading or overlapped, page_num))

    return result
