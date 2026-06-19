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
    """Yield a temporary PDF file path containing text content across pages."""
    buf = _make_pdf(["Page one content\n\nSection Two\n\nMore content here"])
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(buf)
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture()
def pdf_with_images() -> Generator[str, None, None]:
    """Yield a temporary PDF file path containing an embedded image."""
    pages = ["Page with an image"]
    buf = _make_pdf(pages, with_image=True)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(buf)
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture()
def empty_pdf() -> Generator[str, None, None]:
    """Yield a temporary PDF file path with blank pages and no content."""
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
    """Yield a temporary .txt file path with three sections of repeated text."""
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
    with tempfile.NamedTemporaryFile(
        suffix=".txt", mode="w", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        path = f.name
    yield path
    os.unlink(path)


def _write_temp(content: str, suffix: str) -> str:
    with tempfile.NamedTemporaryFile(
        suffix=suffix, mode="w", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        path = f.name
    return path


@pytest.fixture()
def md_file() -> Generator[str, None, None]:
    """Yield a temporary .md file path with multiple heading levels."""
    content = (
        "# Heading 1\n\nContent under heading 1\n\n## Heading 2\n\n"
        "Content under heading 2\n\nMore content\n\n# Heading 3\n\nFinal section"
    )
    path = _write_temp(content, ".md")
    yield path
    os.unlink(path)


@pytest.fixture()
def invalid_pdf() -> Generator[str, None, None]:
    """Yield a temporary file path with .pdf extension but invalid content."""
    path = _write_temp("not a real pdf", ".pdf")
    yield path
    os.unlink(path)


# ---------------------------------------------------------------------------
# Token count
# ---------------------------------------------------------------------------


class TestCountTokens:
    """Tests for the count_tokens helper."""
    def test_empty_string(self) -> None:
        """Verify count_tokens returns 1 for an empty string."""
        assert count_tokens("") == 1

    def test_short_string(self) -> None:
        """Verify count_tokens returns 1 for a short single-token string."""
        assert count_tokens("hello") == 1

    def test_exact_boundary(self) -> None:
        """Verify count_tokens returns 1 at the exact 4-character token boundary."""
        assert count_tokens("a" * 4) == 1

    def test_rough_approximation(self) -> None:
        """Verify count_tokens approximates to 1 token per 4 characters."""
        text = "word " * 100
        assert count_tokens(text) == len(text) // 4


# ---------------------------------------------------------------------------
# Chunk splitting
# ---------------------------------------------------------------------------


class TestSplitBySections:
    """Tests for the split_by_sections chunking function."""
    def test_empty_sections(self) -> None:
        """Verify split_by_sections returns an empty list for no sections."""
        assert split_by_sections([]) == []

    def test_single_small_section(self) -> None:
        """Verify a single small section returns as one chunk with unchanged text."""
        result = split_by_sections([("hello world", None)])
        assert len(result) == 1
        assert result[0][0] == "hello world"

    def test_multiple_sections(self) -> None:
        """Verify multiple small sections are concatenated into a single chunk."""
        sections = [("sec1", None), ("sec2", None), ("sec3", None)]
        result = split_by_sections(sections)
        assert len(result) == 1
        texts = result[0][0]
        assert "sec1" in texts
        assert "sec2" in texts
        assert "sec3" in texts

    def test_overlap_between_chunks(self) -> None:
        """Verify adjacent chunks share OVERLAP_CHARS of trailing text."""
        long_section = "A" * (MAX_CHARS + 100)
        result = split_by_sections([(long_section, None)])
        assert len(result) >= 2
        assert len(result[0][0]) <= MAX_CHARS + OVERLAP_CHARS
        tail = result[0][0][-OVERLAP_CHARS:]
        assert tail in result[1][0]

    def test_very_long_single_section_paragraph_split(self) -> None:
        """Verify a long section is split on paragraph boundaries."""
        section = ("paragraph one. " * 200) + "\n\n" + ("paragraph two. " * 200)
        result = split_by_sections([(section, None)])
        assert len(result) >= 1

    def test_no_heading_metadata(self) -> None:
        """Verify a section with no heading stores the text as its own metadata."""
        result = split_by_sections([("just a single section", None)])
        assert result[0][1] == "just a single section"


# ---------------------------------------------------------------------------
# Simple parser (TXT / MD)
# ---------------------------------------------------------------------------


class TestSimpleParser:
    """Tests for parse_simple (TXT / MD parser)."""
    def test_txt_section_split(self, txt_file: str) -> None:
        """Verify parse_simple splits .txt into sections, preserving all names."""
        chunks = parse_simple(txt_file)
        assert len(chunks) >= 2
        text_chunks = [c for c in chunks if isinstance(c, TextChunk)]
        texts = "".join(c.text_content for c in text_chunks)
        assert "SECTION ONE" in texts
        assert "SECTION TWO" in texts
        assert "SECTION THREE" in texts

    def test_md_heading_split(self, md_file: str) -> None:
        """Verify parse_simple splits a .md file on markdown headings."""
        chunks = parse_simple(md_file)
        assert any(
            "Heading 1" in c.text_content for c in chunks if isinstance(c, TextChunk)
        )

    def test_empty_file(self) -> None:
        """Verify parse_simple returns no chunks for an empty file."""
        path = _write_temp("", ".txt")
        try:
            chunks = parse_simple(path)
            assert len(chunks) == 0
        finally:
            os.unlink(path)

    def test_no_heading_plain_text(self) -> None:
        """Verify parse_simple returns one TEXT chunk for plain text w/out headings."""
        path = _write_temp(
            "Just some plain text without any headings or structure.", ".txt"
        )
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
    """Tests for parse_pdf (PDF parser)."""
    def test_text_extraction(self, pdf_with_text: str) -> None:
        """Verify parse_pdf extracts text content from PDF pages."""
        chunks = parse_pdf(pdf_with_text)
        texts = " ".join(c.text_content for c in chunks if isinstance(c, TextChunk))
        assert "Page one content" in texts
        assert "More content here" in texts

    def test_image_detection(self, pdf_with_images: str) -> None:
        """Verify parse_pdf detects embedded images in a PDF."""
        chunks = parse_pdf(pdf_with_images)
        images = [c for c in chunks if isinstance(c, ImageChunk)]
        assert len(images) > 0

    def test_image_chunk_fields(self, pdf_with_images: str) -> None:
        """Verify ImageChunk fields image_data and image_mime_type are populated."""
        chunks = parse_pdf(pdf_with_images)
        images = [c for c in chunks if isinstance(c, ImageChunk)]
        assert len(images) > 0
        img = images[0]
        assert isinstance(img.image_data, bytes)
        assert len(img.image_data) > 0
        assert img.image_mime_type.startswith("image/")

    def test_empty_pdf(self, empty_pdf: str) -> None:
        """Verify parse_pdf returns no chunks for a blank PDF with no content."""
        chunks = parse_pdf(empty_pdf)
        assert len(chunks) == 0

    def test_source_filename_set(self, pdf_with_text: str) -> None:
        """Verify each chunk's source_filename ends with .pdf."""
        chunks = parse_pdf(pdf_with_text)
        for c in chunks:
            if c.source_filename:
                assert c.source_filename.endswith(".pdf")

    def test_doc_id_format(self, pdf_with_text: str) -> None:
        """Verify each chunk's doc_id starts with 'pdf:' and contains a colon."""
        chunks = parse_pdf(pdf_with_text)
        for c in chunks:
            assert c.doc_id.startswith("pdf:")
            assert ":" in c.doc_id


# ---------------------------------------------------------------------------
# Parser routing
# ---------------------------------------------------------------------------


class TestParseDocument:
    """Tests for parse_document (parser routing/dispatch)."""
    def test_routes_pdf_by_mime(self, pdf_with_text: str) -> None:
        """Verify parse_document routes .pdf files to the PDF parser by MIME type."""
        chunks = parse_document(pdf_with_text)
        assert len(chunks) > 0

    def test_routes_txt_by_mime(self, txt_file: str) -> None:
        """Verify parse_document routes .txt files to the simple parser by MIME type."""
        chunks = parse_document(txt_file)
        assert len(chunks) > 0
        assert all(c.chunk_type == ChunkType.TEXT for c in chunks)

    def test_routes_md_by_mime(self, md_file: str) -> None:
        """Verify parse_document routes .md files to the simple parser by MIME type."""
        chunks = parse_document(md_file)
        assert len(chunks) > 0
        assert all(c.chunk_type == ChunkType.TEXT for c in chunks)

    def test_override_to_simple(self, pdf_with_text: str) -> None:
        """Verify parser override to 'simple' works on a .pdf file."""
        chunks = parse_document(pdf_with_text, source_metadata={"parser": "simple"})
        assert len(chunks) > 0

    def test_override_to_pdf(self, txt_file: str) -> None:
        """Verify parser override to 'pdf' works on a .txt file."""
        chunks = parse_document(txt_file, source_metadata={"parser": "pdf"})
        assert len(chunks) > 0

    def test_nonexistent_file(self) -> None:
        """Verify parse_document raises ParseError for a nonexistent file path."""
        from rag_wiki.exceptions import ParseError

        with pytest.raises(ParseError):
            parse_document("/nonexistent/file.pdf")

    def test_empty_source_metadata(self, txt_file: str) -> None:
        """Verify parse_document works with an empty source_metadata dict."""
        chunks = parse_document(txt_file, source_metadata={})
        assert len(chunks) > 0

    def test_unknown_engine_override(self, pdf_with_text: str) -> None:
        """Verify parse_document raises ParseError for an unknown parser override."""
        from rag_wiki.exceptions import ParseError

        with pytest.raises(ParseError):
            parse_document(pdf_with_text, source_metadata={"parser": "nonexistent"})


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Tests for error handling in parse_document and parse_pdf."""
    def test_unreadable_file(self) -> None:
        """Verify parse_document raises ParseError for an unreadable file path."""
        from rag_wiki.exceptions import ParseError

        with pytest.raises(ParseError):
            parse_document("/dev/null/nope")

    def test_empty_file_path(self) -> None:
        """Verify parse_document raises ParseError when the file has been deleted."""
        from rag_wiki.exceptions import ParseError

        path = _write_temp("", ".txt")
        os.unlink(path)
        with pytest.raises(ParseError):
            parse_document(path)

    def test_invalid_pdf_bytes(self, invalid_pdf: str) -> None:
        """Verify parse_pdf raises fitz.FileDataError for invalid PDF content."""
        with pytest.raises(fitz.FileDataError):
            parse_pdf(invalid_pdf)
