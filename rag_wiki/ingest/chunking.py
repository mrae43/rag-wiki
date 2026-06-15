from __future__ import annotations

MAX_TOKENS = 512
OVERLAP_TOKENS = 64
CHARS_PER_TOKEN = 4
MAX_CHARS = MAX_TOKENS * CHARS_PER_TOKEN  # 2048
OVERLAP_CHARS = OVERLAP_TOKENS * CHARS_PER_TOKEN  # 256


def count_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def semantic_overlap(prev_section: str, next_section: str, overlap_chars: int = OVERLAP_CHARS) -> str:
    tail = prev_section[-overlap_chars:] if len(prev_section) > overlap_chars else prev_section
    return tail + next_section


def _split_paragraph(text: str) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return paragraphs if paragraphs else [text]


def split_by_sections(
    sections: list[str],
    max_chars: int = MAX_CHARS,
    overlap_chars: int = OVERLAP_CHARS,
) -> list[tuple[str, str | None]]:
    result: list[tuple[str, str | None]] = []
    if not sections:
        return result

    buffer = ""
    buffer_section: str | None = None

    for section in sections:
        if len(section) > max_chars:
            if buffer:
                result.append((buffer, buffer_section))
                buffer = ""
                buffer_section = None
            paragraphs = _split_paragraph(section)
            for para in paragraphs:
                if len(para) <= max_chars:
                    if buffer:
                        result.append((buffer, buffer_section))
                    buffer = para
                    buffer_section = para
                else:
                    if buffer:
                        result.append((buffer, buffer_section))
                    for i in range(0, len(para), max_chars - overlap_chars):
                        chunk = para[i : i + max_chars]
                        if chunk:
                            heading = para[: min(80, len(para) // 4)] if i == 0 else None
                            result.append((chunk, heading))
                    buffer = ""
                    buffer_section = None
            continue

        if len(buffer) + len(section) + 2 <= max_chars:
            if buffer:
                buffer += "\n\n" + section
            else:
                buffer = section
                buffer_section = section
            continue

        if buffer:
            result.append((buffer, buffer_section))
        buffer = section
        buffer_section = section

    if buffer:
        result.append((buffer, buffer_section))

    result = _apply_overlap(result, overlap_chars)
    return result


def _apply_overlap(
    chunks: list[tuple[str, str | None]],
    overlap_chars: int,
) -> list[tuple[str, str | None]]:
    if len(chunks) <= 1:
        return chunks

    result: list[tuple[str, str | None]] = [chunks[0]]
    for i in range(1, len(chunks)):
        prev_text = chunks[i - 1][0]
        curr_text, heading = chunks[i]
        tail = prev_text[-overlap_chars:] if len(prev_text) > overlap_chars else prev_text
        overlapped = tail + curr_text
        result.append((overlapped, heading or overlapped))

    return result
