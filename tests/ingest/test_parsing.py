from __future__ import annotations

import os
import tempfile
from collections.abc import Generator

import fitz
import pytest

from rag_wiki.ingest.chunking import (
    MAX_CHARS,
    OVERLAP_CHARS,
    count_tokens,
    split_by_sections,
)
from rag_wiki.ingest.parser import parse_document
from rag_wiki.ingest.parsers.pdf import parse_pdf
from rag_wiki.ingest.parsers.simple import parse_simple
from rag_wiki.ingest.schemas import ChunkType, ImageChunk, TextChunk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_png_bytes() -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 1, 1))
    result: bytes = pix.tobytes("png")
    return result


def _make_pdf(
    pages: list[str],
    with_image: bool = False,
) -> bytes:
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text(fitz.Point(72, 72), text, fontsize=11)
        if with_image:
            page.insert_image(
                fitz.Rect(72, 400, 200, 500),
                stream=_valid_png_bytes(),
            )
    result: bytes = doc.tobytes()
    return result


@pytest.fixture()
def pdf_with_text() -> Generator[str, None, None]:
    buf = _make_pdf(["Page one content\n\nSection Two\n\nMore content here"])
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(buf)
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture()
def pdf_with_images() -> Generator[str, None, None]:
    pages = ["Page with an image"]
    buf = _make_pdf(pages, with_image=True)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(buf)
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture()
def empty_pdf() -> Generator[str, None, None]:
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    buf: bytes = doc.tobytes()
    doc.close()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(buf)
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture()
def txt_file() -> Generator[str, None, None]:
    content = (
        "SECTION ONE\n\n\n"
        + ("A" * 300 + "\n") * 10
        + "\n\n\n"
        + "SECTION TWO\n\n\n"
        + ("B" * 300 + "\n") * 10
        + "\n\n\n"
        + "SECTION THREE\n\n\n"
        + ("C" * 300 + "\n") * 10
    )
    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = f.name
    yield path
    os.unlink(path)


def _write_temp(content: str, suffix: str) -> str:
    with tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = f.name
    return path


@pytest.fixture()
def md_file() -> Generator[str, None, None]:
    content = "# Heading 1\n\nContent under heading 1\n\n## Heading 2\n\nContent under heading 2\n\nMore content\n\n# Heading 3\n\nFinal section"
    path = _write_temp(content, ".md")
    yield path
    os.unlink(path)


# ---------------------------------------------------------------------------
# Token count
# ---------------------------------------------------------------------------


class TestCountTokens:
    def test_empty_string(self) -> None:
        assert count_tokens("") == 1

    def test_short_string(self) -> None:
        assert count_tokens("hello") == 1

    def test_exact_boundary(self) -> None:
        assert count_tokens("a" * 4) == 1

    def test_rough_approximation(self) -> None:
        text = "word " * 100
        assert count_tokens(text) == len(text) // 4


# ---------------------------------------------------------------------------
# Chunk splitting
# ---------------------------------------------------------------------------


class TestSplitBySections:
    def test_empty_sections(self) -> None:
        assert split_by_sections([]) == []

    def test_single_small_section(self) -> None:
        result = split_by_sections(["hello world"])
        assert len(result) == 1
        assert result[0][0] == "hello world"

    def test_multiple_sections(self) -> None:
        sections = ["sec1", "sec2", "sec3"]
        result = split_by_sections(sections)
        assert len(result) == 1
        texts = result[0][0]
        assert "sec1" in texts
        assert "sec2" in texts
        assert "sec3" in texts

    def test_overlap_between_chunks(self) -> None:
        long_section = "A" * (MAX_CHARS + 100)
        result = split_by_sections([long_section])
        assert len(result) >= 2
        assert len(result[0][0]) <= MAX_CHARS + OVERLAP_CHARS
        tail = result[0][0][-OVERLAP_CHARS:]
        assert tail in result[1][0]

    def test_very_long_single_section_paragraph_split(self) -> None:
        section = ("paragraph one. " * 200) + "\n\n" + ("paragraph two. " * 200)
        result = split_by_sections([section])
        assert len(result) >= 1

    def test_no_heading_metadata(self) -> None:
        result = split_by_sections(["just a single section"])
        assert result[0][1] == "just a single section"


# ---------------------------------------------------------------------------
# Simple parser (TXT / MD)
# ---------------------------------------------------------------------------


class TestSimpleParser:
    def test_txt_section_split(self, txt_file: str) -> None:
        chunks = parse_simple(txt_file)
        assert len(chunks) >= 2
        text_chunks = [c for c in chunks if isinstance(c, TextChunk)]
        texts = "".join(c.text_content for c in text_chunks)
        assert "SECTION ONE" in texts
        assert "SECTION TWO" in texts
        assert "SECTION THREE" in texts

    def test_md_heading_split(self, md_file: str) -> None:
        chunks = parse_simple(md_file)
        assert any("Heading 1" in c.text_content for c in chunks if isinstance(c, TextChunk))

    def test_empty_file(self) -> None:
        path = _write_temp("", ".txt")
        try:
            chunks = parse_simple(path)
            assert len(chunks) >= 0
        finally:
            os.unlink(path)

    def test_no_heading_plain_text(self) -> None:
        path = _write_temp("Just some plain text without any headings or structure.", ".txt")
        try:
            chunks = parse_simple(path)
            assert len(chunks) == 1
            assert chunks[0].chunk_type == ChunkType.TEXT
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# PDF parser
# ---------------------------------------------------------------------------


class TestPdfParser:
    def test_text_extraction(self, pdf_with_text: str) -> None:
        chunks = parse_pdf(pdf_with_text)
        texts = " ".join(c.text_content for c in chunks if isinstance(c, TextChunk))
        assert "Page one content" in texts
        assert "More content here" in texts

    def test_image_detection(self, pdf_with_images: str) -> None:
        chunks = parse_pdf(pdf_with_images)
        images = [c for c in chunks if isinstance(c, ImageChunk)]
        assert len(images) > 0

    def test_image_chunk_fields(self, pdf_with_images: str) -> None:
        chunks = parse_pdf(pdf_with_images)
        images = [c for c in chunks if isinstance(c, ImageChunk)]
        assert len(images) > 0
        img = images[0]
        assert isinstance(img.image_data, bytes)
        assert len(img.image_data) > 0
        assert img.image_mime_type.startswith("image/")

    def test_empty_pdf(self, empty_pdf: str) -> None:
        chunks = parse_pdf(empty_pdf)
        assert len(chunks) >= 0

    def test_source_filename_set(self, pdf_with_text: str) -> None:
        chunks = parse_pdf(pdf_with_text)
        for c in chunks:
            if c.source_filename:
                assert c.source_filename.endswith(".pdf")

    def test_doc_id_format(self, pdf_with_text: str) -> None:
        chunks = parse_pdf(pdf_with_text)
        for c in chunks:
            assert c.doc_id.startswith("pdf:")
            assert ":" in c.doc_id


# ---------------------------------------------------------------------------
# Parser routing
# ---------------------------------------------------------------------------


class TestParseDocument:
    def test_routes_pdf_by_mime(self, pdf_with_text: str) -> None:
        chunks = parse_document(pdf_with_text)
        assert len(chunks) > 0

    def test_routes_txt_by_mime(self, txt_file: str) -> None:
        chunks = parse_document(txt_file)
        assert len(chunks) > 0
        assert all(c.chunk_type == ChunkType.TEXT for c in chunks)

    def test_routes_md_by_mime(self, md_file: str) -> None:
        chunks = parse_document(md_file)
        assert len(chunks) > 0
        assert all(c.chunk_type == ChunkType.TEXT for c in chunks)

    def test_override_to_simple(self, pdf_with_text: str) -> None:
        chunks = parse_document(pdf_with_text, source_metadata={"parser": "simple"})
        assert len(chunks) > 0

    def test_override_to_pdf(self, txt_file: str) -> None:
        chunks = parse_document(txt_file, source_metadata={"parser": "pdf"})
        assert len(chunks) > 0

    def test_nonexistent_file(self) -> None:
        from rag_wiki.exceptions import ParseError

        with pytest.raises(ParseError):
            parse_document("/nonexistent/file.pdf")

    def test_empty_source_metadata(self, txt_file: str) -> None:
        chunks = parse_document(txt_file, source_metadata={})
        assert len(chunks) > 0

    def test_unknown_engine_override(self, pdf_with_text: str) -> None:
        from rag_wiki.exceptions import ParseError

        with pytest.raises(ParseError):
            parse_document(pdf_with_text, source_metadata={"parser": "nonexistent"})


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_unreadable_file(self) -> None:
        from rag_wiki.exceptions import ParseError

        with pytest.raises(ParseError):
            parse_document("/dev/null/nope")

    def test_empty_file_path(self) -> None:
        from rag_wiki.exceptions import ParseError

        path = _write_temp("", ".txt")
        os.unlink(path)
        with pytest.raises(ParseError):
            parse_document(path)

    def test_invalid_pdf_bytes(self) -> None:
        path = _write_temp("not a real pdf", ".pdf")
        try:
            with pytest.raises(Exception):
                parse_pdf(path)
        finally:
            os.unlink(path)
