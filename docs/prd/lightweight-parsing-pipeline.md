# PRD: Lightweight Parsing Pipeline

## Problem Statement

The ingest pipeline is unimplemented — parsing code is the first real code to be
written in the pipeline. The existing ADR-0002 calls for a hybrid parsing
approach (lightweight default, optional MinerU), but no concrete design or
implementation exists for the lightweight path. Without it, sources cannot be
parsed into typed chunks, blocking the entire downstream pipeline (extraction,
embedding, knowledge graph, wiki synthesis).

## Solution

A lightweight document parsing pipeline that accepts all source file types and
produces typed, section-bounded chunks (text, table, image). The pipeline uses
a MIME-type-routed dispatch table with automatic fallback, backend-specific
section detection, and a unified Pydantic output schema. Chunks target 512
tokens with a 64-token overlap, split at semantic section boundaries.

## User Stories

1. As a user ingesting a PDF, I want text to be extracted and split at heading
   boundaries (detected via font-size heuristics), so that each chunk covers a
   coherent topic.

2. As a user ingesting a PDF, I want embedded tables to be extracted and typed
   as `TableChunk` (markdown representation), so that tabular data is preserved
   as structured content rather than raw text.

3. As a user ingesting a PDF, I want embedded images to be extracted and typed
   as `ImageChunk` (raw bytes, mime type), so that images can later be embedded
   via a multimodal embedding model rather than an OCR/captioning step.

4. As a user ingesting a scanned PDF, I want automatic OCR fallback via
   PyMuPDF OCR when text extraction yields fewer than 50 characters, so that
   image-only documents are still parseable.

5. As a user ingesting a DOCX file, I want sections detected via heading styles
   (Heading 1, Heading 2) and split at those boundaries, so that document
   structure is preserved.

6. As a user ingesting an HTML file, I want section headers detected via `<h1>`,
   `<h2>` tags and split accordingly.

7. As a user ingesting plain text or markdown, I want sections detected via
   blank-line breaks (`# ` headings for markdown) and split at those boundaries.

8. As a user overriding the parser, I want to pass `{"parser": "ocr"}` in
   source metadata to force OCR path on a per-document basis.

9. As a developer, I want the parser to be a pure function that returns
   `list[ParsedChunk]` without writing to the database, so that I can unit-test
   parsing in isolation.

10. As a developer, I want a clear error boundary — unrecoverable parse failures
    produce a typed `ParseError` and set the source status to `FAILED`, not a
    crash or silent skip.

11. As a developer, I want the existing DB migration to be updated (new migration
    adding chunk_type, image_url, image_mime_type, metadata_ columns; changing
    text_content to nullable; bumping vector dimensions to 3072), so that the
    schema matches the new chunk types.

12. As a developer, I want the embedding model default changed to
    `gemini-embedding-2` and dimensions to 3072 in settings, so that new
    deployments use the multimodal embedding model by default.

## Implementation Decisions

### Module structure

- `rag_wiki/ingest/schemas.py` — Pydantic chunk type definitions
- `rag_wiki/ingest/parser.py` — top-level `parse_document()` routing function
- `rag_wiki/ingest/chunking.py` — token counting + semantic splitting utilities
- `rag_wiki/ingest/parsers/pdf.py` — PyMuPDF parser with OCR fallback
- `rag_wiki/ingest/parsers/unstructured.py` — unstructured parser (DOCX, HTML, etc.)
- `rag_wiki/ingest/parsers/simple.py` — TXT/MD parser (no external deps)

No DB writes inside any parser — all parsers return `list[ParsedChunk]`.
Errors produce typed `ParseError(IngestError)`.

### Parser routing (`rag_wiki/ingest/parser.py`)

```
parse_document(source, file_path) -> list[ParsedChunk]

MIME-type dispatch:
  application/pdf          → pdf.py
  text/plain, text/markdown → simple.py
  everything else          → unstructured.py

Override: source.metadata.get("parser") → "pdf" | "unstructured" | "simple" | "ocr"
Fallback: pdf.py → PyMuPDF OCR on text yield < 50 chars
Failure:  all paths exhausted → ParseError
```

### Chunk types (`rag_wiki/ingest/schemas.py`)

Discriminated union keyed on `chunk_type`:

```python
class ChunkType(str, Enum):
    TEXT  = "text"
    TABLE = "table"
    IMAGE = "image"

class BaseChunk(BaseModel):
    doc_id:          str
    chunk_type:      ChunkType
    page_number:     int | None = None
    source_filename: str | None = None
    metadata:        dict = {}

class TextChunk(BaseChunk):
    chunk_type:   Literal[ChunkType.TEXT] = ChunkType.TEXT
    text_content: str

class TableChunk(BaseChunk):
    chunk_type:   Literal[ChunkType.TABLE] = ChunkType.TABLE
    text_content: str    # markdown representation
    headers:      list[str] = []

class ImageChunk(BaseChunk):
    chunk_type:      Literal[ChunkType.IMAGE] = ChunkType.IMAGE
    image_data:      bytes
    image_mime_type: str
    caption:         str | None = None

class ParsedChunk = Annotated[
    Union[TextChunk, TableChunk, ImageChunk],
    Field(discriminator="chunk_type")
]
```

`ImageChunk.image_data` carries raw bytes in-memory only — never persisted.
Upload to blob storage is deferred to the embed step (next iteration).

### Chunking strategy (`rag_wiki/ingest/chunking.py`)

- Token count: rough estimate (4 characters ≈ 1 token)
- Max chunk: 512 tokens (~2048 characters)
- Overlap: 64 tokens (~256 characters)
- Split: at section heading boundaries, never mid-paragraph
- No section heading found → fall back to paragraph boundary split
- Single section exceeds 512 tokens → recursively split at paragraph boundaries,
  retaining overlap

Section detection is backend-specific — each parser implements its own heading
detection (font-size for PyMuPDF, heading styles for unstructured, `# ` for
markdown, blank-line clusters for plain text).

### PDF parser (`rag_wiki/ingest/parsers/pdf.py`)

- Uses PyMuPDF (`fitz`) for text, table, and image extraction
- Heading detection: font-size delta heuristic (heading if font size ≥ 1.5× body)
- Table detection: PyMuPDF built-in `find_tables()`, exported as markdown
- Image detection: `get_page_images()` → extract raw bytes + mime type
- OCR fallback: if full-page text yield < 50 chars, re-run with PyMuPDF OCR
  (`page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)`) + Tesseract

### Simple parser (`rag_wiki/ingest/parsers/simple.py`)

- TXT: section marker heuristic (blank-line-separated clusters, lines ending
  with colon, all-caps single lines as heading candidates)
- MD: `# ` / `## ` heading detection

### Settings

```python
# Existing, change default:
embedding_model: str = "gemini-embedding-2"      # was "text-embedding-3-small"
embedding_dimensions: int = 3072                  # was 1536
```

The `llm_model_caption` field is preserved but will be removed in a future
iteration when `caption_image()` is removed from the `LLMProvider` protocol.

### DB migration (new Alembic revision)

`chunks` table modifications (in one migration):

| Change | Detail |
|---|---|
| ADD `chunk_type` | `Text`, not null, default `'text'` |
| ADD `image_url` | `Text`, nullable |
| ADD `image_mime_type` | `Text`, nullable |
| ADD `metadata_` | `JSONB`, nullable |
| ALTER `text_content` | Drop `NOT NULL` (image chunks have no text) |
| ALTER `embedding` | `Vector(1536)` → `Vector(3072)` |
| Drop + recreate HNSW index | Dimensions changed |

`entities` table modifications:

| Change | Detail |
|---|---|
| ALTER `embedding` | `Vector(1536)` → `Vector(3072)` |
| Drop + recreate HNSW index | Dimensions changed |

The HNSW index must be dropped before the `ALTER COLUMN TYPE` and recreated
after — pgvector does not allow altering the dimension of a column that has an
HNSW index.

## Testing Decisions

### What makes a good test

- Tests the external behavior of each parser (input file → list of ParsedChunks
  with correct types, sizes, and boundaries), not internal helper functions
- Uses real or near-real file fixtures (small PDF, minimal DOCX, sample MD)
- Tests error paths (scanned PDF that neither PyMuPDF nor OCR can handle →
  ParseError)
- Tests boundary conditions (single section exceeding 512 tokens, empty source,
  no headings found)
- Tests the overlap behavior (last 64 tokens of chunk N appear in chunk N+1)

### Modules to test

- `rag_wiki/ingest/parsers/pdf.py` — PDF text, table, image extraction; OCR fallback
- `rag_wiki/ingest/parsers/unstructured.py` — DOCX/HTML heading detection
- `rag_wiki/ingest/parsers/simple.py` — TXT/MD section splitting
- `rag_wiki/ingest/chunking.py` — token count estimation, split boundaries, overlap
- `rag_wiki/ingest/parser.py` — MIME routing, override dispatch, fallback chain

### Prior art

- `tests/db/test_models.py` — tests ORM CRUD round-trips for Source + Chunk
- `tests/conftest.py` — session-scoped test DB with per-test transactions

New test file: `tests/ingest/test_parsing.py`

## Out of Scope

- MinIO/Docker service setup, `BlobStore` abstraction, and image upload
- `MultimodalEmbeddingProvider` interface and Gemini Embedding 2 client
- Removal of `caption_image()` from `LLMProvider` protocol
- `EmbeddedChunk` schema and the embed step (chunk → vector)
- Worker job orchestration (job queue wiring)
- The MinerU parser path (ADR-0002 optional)
- Integration tests that span parse → embed → extract (next iteration)

## Further Notes

The image upload + embed step is the natural next iteration. The `ImageChunk`
schema carries raw `image_data` bytes specifically so the embed step can upload
them to blob storage without re-reading the source file. Once uploaded, the
`image_url` replaces `image_data` and the row is written to the DB as an
`EmbeddedChunk`.

The `llm_model_caption` setting and `caption_image()` method are preserved in
this iteration for backward compatibility with any existing provider
implementations. They will be removed when the multimodal embedding provider
abstraction is introduced.
