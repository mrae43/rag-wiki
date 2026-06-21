from __future__ import annotations

import enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class ChunkType(enum.StrEnum):
    """Discriminated union tag for parsed chunk variants."""

    TEXT = "text"
    TABLE = "table"
    IMAGE = "image"


class BaseChunk(BaseModel):
    """Common fields shared by all parsed chunk types."""

    doc_id: str
    chunk_type: ChunkType
    page_number: int | None = None
    source_filename: str | None = None
    metadata: dict[str, Any] = {}


class TextChunk(BaseChunk):
    """A chunk containing raw extracted text."""

    chunk_type: Literal[ChunkType.TEXT] = ChunkType.TEXT
    text_content: str


class TableChunk(BaseChunk):
    """A chunk representing a table, with text content and column headers."""

    chunk_type: Literal[ChunkType.TABLE] = ChunkType.TABLE
    text_content: str
    headers: list[str] = []


class ImageChunk(BaseChunk):
    """A chunk containing binary image data and an optional caption."""

    chunk_type: Literal[ChunkType.IMAGE] = ChunkType.IMAGE
    image_data: bytes
    image_mime_type: str
    caption: str | None = None


ParsedChunk = Annotated[
    TextChunk | TableChunk | ImageChunk,
    Field(discriminator="chunk_type"),
]
