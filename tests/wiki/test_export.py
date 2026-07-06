"""
tests.wiki.test_export
----------------------
Tests for the OKF export module: front-matter builder, manifest, log writer,
page renderer, orphan deletion, and supporting helpers.

Pure-function tests are synchronous; storage-dependent tests use
FakeStorageProvider.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rag_wiki.db.models.graph import Entity, PublishedStatus
from rag_wiki.db.models.wiki import WikiPage
from rag_wiki.exceptions import StorageError
from rag_wiki.storage.base import StorageProvider
from rag_wiki.storage.local import LocalStorageProvider
from rag_wiki.wiki.export import (
    _build_front_matter,
    _build_page_info,
    _delete_orphan_pages,
    _first_paragraph_excerpt,
    _hash_content,
    _LogWriter,
    _Manifest,
    _page_kind,
    _page_path,
    _render_dir_index,
    _render_page,
    _render_root_index,
    export_bundle,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_entity() -> MagicMock:
    """Return a mock entity with sensible defaults."""
    entity = MagicMock()
    entity.id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    entity.name = "Acme Corp"
    entity.entity_type = "organization"
    entity.description = "A fictional corporation."
    return entity


@pytest.fixture
def entity_page(mock_entity: MagicMock) -> WikiPage:
    """Return a WikiPage representing an entity page."""
    page = MagicMock(spec=WikiPage)
    page.entity_id = mock_entity.id
    page.entity = mock_entity
    page.slug = "acme-corp-11111111"
    page.title = "Acme Corp"
    page.content = "**Acme Corp** is a fictional organization.\n\nSee [[other-entity]]."
    page.synthesized_at = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
    page.synthesized_from_sources = None
    page.status = PublishedStatus.PUBLISHED
    return page


@pytest.fixture
def source_page() -> WikiPage:
    """Return a WikiPage representing a source-summary page."""
    page = MagicMock(spec=WikiPage)
    page.entity_id = None
    page.entity = None
    page.slug = "meeting-notes-22222222"
    page.title = "Meeting Notes"
    page.content = (
        "Discussed project roadmap.\n\n"
        "Key decisions recorded in [[acme-corp-11111111]]."
    )
    page.synthesized_at = datetime(2026, 6, 16, 14, 30, 0, tzinfo=UTC)
    page.synthesized_from_sources = [
        "22222222-2222-2222-2222-222222222222",
    ]
    page.status = PublishedStatus.PUBLISHED
    return page


@pytest.fixture
def slug_map() -> dict[str, str]:
    """Return a typical slug-to-name map."""
    return {
        "acme-corp-11111111": "Acme Corp",
        "meeting-notes-22222222": "meeting-notes.pdf",
        "other-entity": "Other Entity",
    }


# ---------------------------------------------------------------------------
# _hash_content
# ---------------------------------------------------------------------------


class TestHashContent:
    def test_deterministic(self) -> None:
        """Same input always produces the same hash."""
        assert _hash_content("hello") == _hash_content("hello")

    def test_different_inputs_different_hashes(self) -> None:
        """Different inputs produce different hashes."""
        assert _hash_content("hello") != _hash_content("world")

    def test_output_format(self) -> None:
        """Output is a 64-character hex string."""
        h = _hash_content("test")
        assert len(h) == 64
        int(h, 16)  # should not raise


# ---------------------------------------------------------------------------
# _first_paragraph_excerpt
# ---------------------------------------------------------------------------


class TestFirstParagraphExcerpt:
    def test_short_paragraph(self) -> None:
        """Content shorter than max_chars returns unchanged."""
        text = "Hello world."
        assert _first_paragraph_excerpt(text, max_chars=200) == "Hello world."

    def test_truncates_at_word_boundary(self) -> None:
        """Truncation respects word boundaries and appends ellipsis."""
        text = (
            "This is a very long sentence that should get truncated"
            " at the nearest word boundary."
        )
        result = _first_paragraph_excerpt(text, max_chars=30)
        assert len(result) <= 33  # <= 30 chars + "..." = 33
        assert result.endswith("...")
        assert not result.endswith(" ...")

    def test_uses_first_paragraph(self) -> None:
        """Only the first paragraph (before blank line) is used."""
        text = "First para.\n\nSecond para."
        assert _first_paragraph_excerpt(text) == "First para."

    def test_strips_whitespace(self) -> None:
        """Leading/trailing whitespace is stripped from the paragraph."""
        text = "  Hello world.  \n\nMore content."
        assert _first_paragraph_excerpt(text) == "Hello world."

    def test_no_paragraph_break(self) -> None:
        """Content without a blank line is used in full (if short enough)."""
        text = "Single line without breaks."
        assert _first_paragraph_excerpt(text) == "Single line without breaks."

    def test_empty_content(self) -> None:
        """Empty content returns an empty string."""
        assert _first_paragraph_excerpt("") == ""


# ---------------------------------------------------------------------------
# _page_kind and _page_path
# ---------------------------------------------------------------------------


class TestPageKind:
    def test_entity_page(self, entity_page: WikiPage) -> None:
        assert _page_kind(entity_page) == "entity"

    def test_source_page(self, source_page: WikiPage) -> None:
        assert _page_kind(source_page) == "source_summary"


class TestPagePath:
    def test_entity_path(self) -> None:
        assert _page_path("acme", "entity") == "entities/acme.md"

    def test_source_path(self) -> None:
        assert _page_path("notes", "source_summary") == "sources/notes.md"


# ---------------------------------------------------------------------------
# _build_front_matter
# ---------------------------------------------------------------------------


class TestBuildFrontMatter:
    def test_entity_page_front_matter(
        self,
        entity_page: WikiPage,
        slug_map: dict[str, str],
    ) -> None:
        """Entity page front-matter includes the entity type and description."""
        fm = _build_front_matter(entity_page, slug_map, "http://localhost:8000")
        assert "type: organization" in fm
        assert "title: Acme Corp" in fm
        assert "description: A fictional corporation." in fm
        assert "page_kind: entity" in fm
        assert 'resource: "http://localhost:8000/entities/' in fm
        assert "11111111-1111-1111-1111-111111111111" in fm
        assert 'timestamp: "2026-06-15T12:00:00+00:00"' in fm
        assert fm.startswith("---")
        assert fm.endswith("---")

    def test_source_page_front_matter(
        self,
        source_page: WikiPage,
        slug_map: dict[str, str],
    ) -> None:
        """Source-summary page front-matter has rag-wiki:source-summary type."""
        fm = _build_front_matter(source_page, slug_map, "http://localhost:8000")
        assert 'type: "rag-wiki:source-summary"' in fm
        assert "title: Meeting Notes" in fm
        assert "page_kind: source_summary" in fm
        assert 'resource: "http://localhost:8000/sources/' in fm
        assert 'timestamp: "2026-06-16T14:30:00+00:00"' in fm

    def test_entity_without_description(self, slug_map: dict[str, str]) -> None:
        """Front-matter omits description when entity.description is None."""
        entity = MagicMock()
        entity.entity_type = "person"
        entity.description = None
        entity.id = uuid.UUID("33333333-3333-3333-3333-333333333333")
        page = MagicMock(spec=WikiPage)
        page.entity_id = entity.id
        page.entity = entity
        page.slug = "john-33333333"
        page.title = "John"
        page.content = "Some content."
        page.synthesized_at = None
        page.synthesized_from_sources = None

        fm = _build_front_matter(page, slug_map, "http://localhost:8000")
        assert "description" not in fm
        assert "timestamp" not in fm

    def test_source_without_sources(self, slug_map: dict[str, str]) -> None:
        """Source page with no synthesized_from_sources omits resource."""
        page = MagicMock(spec=WikiPage)
        page.entity_id = None
        page.entity = None
        page.slug = "orphan-slug"
        page.title = "Orphan"
        page.content = "Content."
        page.synthesized_at = None
        page.synthesized_from_sources = None

        fm = _build_front_matter(page, slug_map, "http://localhost:8000")
        assert "resource" not in fm

    def test_yaml_special_chars_quoted(self, slug_map: dict[str, str]) -> None:
        """Values containing YAML-special characters are quoted."""
        entity = MagicMock()
        entity.entity_type = "organization"
        entity.description = 'Value with "quotes" and colon: here'
        entity.id = uuid.UUID("44444444-4444-4444-4444-444444444444")
        page = MagicMock(spec=WikiPage)
        page.entity_id = entity.id
        page.entity = entity
        page.slug = "special-44444444"
        page.title = 'Title: Special "chars"'
        page.content = "Content."
        page.synthesized_at = None
        page.synthesized_from_sources = None

        fm = _build_front_matter(page, slug_map, "http://localhost:8000")

        # Colon in title should trigger quoting
        assert 'title: "Title: Special \\"chars\\""' in fm
        assert 'description: "Value with \\"quotes\\" and colon: here"' in fm

    def test_multiline_description_uses_block_scalar(
        self,
        slug_map: dict[str, str],
    ) -> None:
        """Multiline values are emitted as a literal block scalar."""
        entity = MagicMock()
        entity.entity_type = "organization"
        entity.description = "First line.\nSecond line.\nThird line."
        entity.id = uuid.UUID("55555555-5555-5555-5555-555555555555")
        page = MagicMock(spec=WikiPage)
        page.entity_id = entity.id
        page.entity = entity
        page.slug = "multi-55555555"
        page.title = "Multi"
        page.content = "Content."
        page.synthesized_at = None
        page.synthesized_from_sources = None

        fm = _build_front_matter(page, slug_map, "http://localhost:8000")

        assert "description: |" in fm
        assert "  First line." in fm
        assert "  Second line." in fm
        assert "  Third line." in fm
        # The inline key-value form must not appear.
        assert "description: First line." not in fm


# ---------------------------------------------------------------------------
# _render_page
# ---------------------------------------------------------------------------


class TestRenderPage:
    def test_returns_tuple(
        self,
        entity_page: WikiPage,
        slug_map: dict[str, str],
    ) -> None:
        """_render_page returns (path, content, hash)."""
        path, content, h = _render_page(entity_page, slug_map, "http://localhost:8000")
        assert isinstance(path, str)
        assert isinstance(content, str)
        assert isinstance(h, str)
        assert len(h) == 64

    def test_path_entity(self, entity_page: WikiPage, slug_map: dict[str, str]) -> None:
        """Entity page path is entities/{slug}.md."""
        path, _, _ = _render_page(entity_page, slug_map, "http://localhost:8000")
        assert path == "entities/acme-corp-11111111.md"

    def test_path_source(self, source_page: WikiPage, slug_map: dict[str, str]) -> None:
        """Source-summary page path is sources/{slug}.md."""
        path, _, _ = _render_page(source_page, slug_map, "http://localhost:8000")
        assert path == "sources/meeting-notes-22222222.md"

    def test_content_has_front_matter(
        self,
        entity_page: WikiPage,
        slug_map: dict[str, str],
    ) -> None:
        """Rendered content starts with YAML front-matter."""
        _, content, _ = _render_page(entity_page, slug_map, "http://localhost:8000")
        assert content.startswith("---")
        assert "---" in content[3:]

    def test_links_rewritten(
        self,
        entity_page: WikiPage,
        slug_map: dict[str, str],
    ) -> None:
        """[[other-entity]] is rewritten to an OKF markdown link."""
        _, content, _ = _render_page(entity_page, slug_map, "http://localhost:8000")
        assert "[[other-entity]]" not in content
        assert "[Other Entity](../entities/other-entity.md)" in content


# ---------------------------------------------------------------------------
# _Manifest
# ---------------------------------------------------------------------------


class TestManifest:
    async def test_load_empty_when_no_file(
        self,
        mock_storage_provider: StorageProvider,
        tmp_path: Path,
    ) -> None:
        """Loading a non-existent manifest returns an empty manifest."""
        manifest = await _Manifest.load(mock_storage_provider, tmp_path)
        assert manifest._data == {}

    async def test_load_valid_json(
        self,
        mock_storage_provider: StorageProvider,
        tmp_path: Path,
    ) -> None:
        """Loading a valid manifest populates _data."""
        data = {"entities/a.md": "abc123", "sources/b.md": "def456"}
        await mock_storage_provider.write_text(
            ".rag-wiki-export-manifest.json",
            json.dumps(data),
            root_dir=tmp_path,
        )
        manifest = await _Manifest.load(mock_storage_provider, tmp_path)
        assert manifest._data == data

    async def test_load_corrupt_json(
        self,
        mock_storage_provider: StorageProvider,
        tmp_path: Path,
    ) -> None:
        """A corrupt manifest results in an empty manifest (graceful degrade)."""
        await mock_storage_provider.write_text(
            ".rag-wiki-export-manifest.json",
            "not-json",
            root_dir=tmp_path,
        )
        manifest = await _Manifest.load(mock_storage_provider, tmp_path)
        assert manifest._data == {}

    async def test_save_and_reload(
        self,
        mock_storage_provider: StorageProvider,
        tmp_path: Path,
    ) -> None:
        """Saved manifest can be reloaded with identical data."""
        manifest = _Manifest()
        manifest._data = {"entities/a.md": "abc123"}
        await manifest.save(mock_storage_provider, tmp_path)

        loaded = await _Manifest.load(mock_storage_provider, tmp_path)
        assert loaded._data == {"entities/a.md": "abc123"}

    async def test_get(
        self,
        mock_storage_provider: StorageProvider,
        tmp_path: Path,
    ) -> None:
        """get returns the hash for a known path, None for unknown."""
        manifest = _Manifest()
        manifest._data = {"entities/a.md": "abc123"}
        assert manifest.get("entities/a.md") == "abc123"
        assert manifest.get("entities/missing.md") is None

    async def test_setitem(
        self,
        mock_storage_provider: StorageProvider,
        tmp_path: Path,
    ) -> None:
        """__setitem__ adds an entry."""
        manifest = _Manifest()
        manifest["entities/a.md"] = "abc123"
        assert manifest["entities/a.md"] == "abc123"

    async def test_contains(
        self,
        mock_storage_provider: StorageProvider,
        tmp_path: Path,
    ) -> None:
        """__contains__ checks membership."""
        manifest = _Manifest()
        manifest._data = {"entities/a.md": "abc123"}
        assert "entities/a.md" in manifest
        assert "sources/b.md" not in manifest

    async def test_removed_vs_no_removals(self) -> None:
        """removed_vs returns empty when all manifest paths are in new_state."""
        manifest = _Manifest()
        manifest._data = {"entities/a.md": "h1", "entities/b.md": "h2"}
        new_state = {"entities/a.md": "h1", "entities/b.md": "h3"}
        assert manifest.removed_vs(new_state) == set()

    async def test_removed_vs_detects_removals(self) -> None:
        """removed_vs returns paths present in manifest but not in new_state."""
        manifest = _Manifest()
        manifest._data = {
            "entities/a.md": "h1",
            "entities/b.md": "h2",
            "entities/c.md": "h3",
        }
        new_state = {"entities/a.md": "h1"}
        assert manifest.removed_vs(new_state) == {"entities/b.md", "entities/c.md"}

    async def test_removed_vs_empty_manifest(self) -> None:
        """removed_vs returns empty when manifest is empty."""
        manifest = _Manifest()
        new_state = {"entities/a.md": "h1"}
        assert manifest.removed_vs(new_state) == set()


# ---------------------------------------------------------------------------
# _LogWriter
# ---------------------------------------------------------------------------


class TestLogWriter:
    async def test_no_entries_no_write(
        self,
        mock_storage_provider: StorageProvider,
        tmp_path: Path,
    ) -> None:
        """Flush with no entries does not create log.md."""
        writer = _LogWriter(mock_storage_provider, tmp_path)
        await writer.flush()
        with pytest.raises(StorageError):
            await mock_storage_provider.read_text("log.md", root_dir=tmp_path)

    async def test_add_entry_appends_to_buffer(
        self,
        mock_storage_provider: StorageProvider,
        tmp_path: Path,
    ) -> None:
        """add_entry queues an entry without writing to storage."""
        writer = _LogWriter(mock_storage_provider, tmp_path)
        writer.add_entry("added", "entities/a.md", "Page A")
        assert len(writer._entries) == 1
        with pytest.raises(StorageError):
            await mock_storage_provider.read_text("log.md", root_dir=tmp_path)

    async def test_flush_writes_entries(
        self,
        mock_storage_provider: StorageProvider,
        tmp_path: Path,
    ) -> None:
        """flush writes accumulated entries to log.md."""
        writer = _LogWriter(mock_storage_provider, tmp_path)
        writer.add_entry("added", "entities/a.md", "Page A")
        writer.add_entry("modified", "entities/b.md", "Page B")
        await writer.flush()

        log = await mock_storage_provider.read_text("log.md", root_dir=tmp_path)
        assert "## 2026" in log  # timestamp header
        assert "**added** `entities/a.md` — Page A" in log
        assert "**modified** `entities/b.md` — Page B" in log

    async def test_flush_is_idempotent(
        self,
        mock_storage_provider: StorageProvider,
        tmp_path: Path,
    ) -> None:
        """flush clears the buffer; second flush is a no-op."""
        writer = _LogWriter(mock_storage_provider, tmp_path)
        writer.add_entry("added", "entities/a.md", "Page A")
        await writer.flush()
        await writer.flush()  # second flush should not duplicate entries

        log = await mock_storage_provider.read_text("log.md", root_dir=tmp_path)
        assert log.count("**added** `entities/a.md` — Page A") == 1

    async def test_flush_appends_to_existing_log(
        self,
        mock_storage_provider: StorageProvider,
        tmp_path: Path,
    ) -> None:
        """flush appends to an existing log.md rather than overwriting."""
        writer = _LogWriter(mock_storage_provider, tmp_path)
        writer.add_entry("added", "entities/a.md", "Page A")
        await writer.flush()

        writer2 = _LogWriter(mock_storage_provider, tmp_path)
        writer2.add_entry("removed", "entities/b.md", "Page B")
        await writer2.flush()

        log = await mock_storage_provider.read_text("log.md", root_dir=tmp_path)
        assert log.count("Page A") == 1
        assert log.count("Page B") == 1

    async def test_entry_format(
        self,
        mock_storage_provider: StorageProvider,
        tmp_path: Path,
    ) -> None:
        """Entry follows the expected markdown bullet format."""
        writer = _LogWriter(mock_storage_provider, tmp_path)
        writer.add_entry("removed", "sources/old.md", "Old Page")
        await writer.flush()

        log = await mock_storage_provider.read_text("log.md", root_dir=tmp_path)
        assert "- **removed** `sources/old.md` — Old Page" in log


# ---------------------------------------------------------------------------
# _delete_orphan_pages
# ---------------------------------------------------------------------------


class TestDeleteOrphanPages:
    async def test_deletes_orphan_files(
        self,
        mock_storage_provider: StorageProvider,
        tmp_path: Path,
    ) -> None:
        """Known orphan paths are deleted from storage."""
        await mock_storage_provider.write_text(
            "entities/a.md",
            "content",
            root_dir=tmp_path,
        )
        await mock_storage_provider.write_text(
            "sources/b.md",
            "content",
            root_dir=tmp_path,
        )
        # Also write a file that should NOT be deleted
        await mock_storage_provider.write_text(
            "entities/c.md",
            "keep",
            root_dir=tmp_path,
        )

        await _delete_orphan_pages(
            {"entities/a.md", "sources/b.md"},
            mock_storage_provider,
            tmp_path,
        )

        assert not await mock_storage_provider.exists("entities/a.md")
        assert not await mock_storage_provider.exists("sources/b.md")
        assert await mock_storage_provider.exists("entities/c.md")

    async def test_skips_missing_files(
        self,
        mock_storage_provider: StorageProvider,
        tmp_path: Path,
    ) -> None:
        """Missing orphan files are silently skipped."""
        # Should not raise
        await _delete_orphan_pages(
            {"entities/ghost.md"},
            mock_storage_provider,
            tmp_path,
        )

    async def test_empty_set_does_nothing(
        self,
        mock_storage_provider: StorageProvider,
        tmp_path: Path,
    ) -> None:
        """An empty set of paths does nothing."""
        await _delete_orphan_pages(set(), mock_storage_provider, tmp_path)
        # Should not raise


# ---------------------------------------------------------------------------
# _build_page_info
# ---------------------------------------------------------------------------


class TestBuildPageInfo:
    def test_entity_page(
        self,
        entity_page: WikiPage,
        slug_map: dict[str, str],
    ) -> None:
        """Entity page info uses entity.description."""
        info = _build_page_info(entity_page, slug_map)
        assert info["path"] == "entities/acme-corp-11111111.md"
        assert info["slug"] == "acme-corp-11111111"
        assert info["title"] == "Acme Corp"
        assert info["description"] == "A fictional corporation."
        assert info["page_kind"] == "entity"

    def test_source_page(
        self,
        source_page: WikiPage,
        slug_map: dict[str, str],
    ) -> None:
        """Source page info uses first-paragraph excerpt."""
        info = _build_page_info(source_page, slug_map)
        assert info["path"] == "sources/meeting-notes-22222222.md"
        assert info["page_kind"] == "source_summary"
        assert info["description"] == "Discussed project roadmap."

    def test_entity_without_description(
        self,
        slug_map: dict[str, str],
    ) -> None:
        """Entity with no description returns empty-string description."""
        entity = MagicMock()
        entity.entity_type = "person"
        entity.description = None
        entity.id = uuid.UUID("33333333-3333-3333-3333-333333333333")
        page = MagicMock(spec=WikiPage)
        page.entity_id = entity.id
        page.entity = entity
        page.slug = "john-33333333"
        page.title = "John"
        page.content = "Some content."
        page.synthesized_from_sources = None
        info = _build_page_info(page, slug_map)
        assert info["description"] == ""


# ---------------------------------------------------------------------------
# _render_root_index / _render_dir_index
# ---------------------------------------------------------------------------


class TestRenderIndex:
    def test_root_index_with_both_kinds(
        self,
        entity_page: WikiPage,
        source_page: WikiPage,
        slug_map: dict[str, str],
    ) -> None:
        """Root index lists both entity and source pages."""
        text = _render_root_index([entity_page, source_page], slug_map)
        assert text.startswith("# Wiki Index")
        assert "## Entities" in text
        assert "## Source Summaries" in text
        assert "Acme Corp" in text
        assert "Meeting Notes" in text

    def test_root_index_empty(
        self,
        slug_map: dict[str, str],
    ) -> None:
        """Root index with no pages has no section headings."""
        text = _render_root_index([], slug_map)
        assert "## Entities" not in text
        assert "## Source Summaries" not in text

    def test_dir_index_entity(
        self,
        entity_page: WikiPage,
        source_page: WikiPage,
        slug_map: dict[str, str],
    ) -> None:
        """Entity directory index lists only entity pages."""
        text = _render_dir_index([entity_page, source_page], "entity", slug_map)
        assert text.startswith("# Entities")
        assert "Acme Corp" in text
        assert "Meeting Notes" not in text
        # Links are relative within directory
        assert "acme-corp-11111111.md" in text
        assert "../entities/" not in text

    def test_dir_index_source(
        self,
        entity_page: WikiPage,
        source_page: WikiPage,
        slug_map: dict[str, str],
    ) -> None:
        """Source directory index lists only source-summary pages."""
        text = _render_dir_index([entity_page, source_page], "source_summary", slug_map)
        assert text.startswith("# Source Summaries")
        assert "Meeting Notes" in text
        assert "Acme Corp" not in text

    def test_dir_index_empty(
        self,
        slug_map: dict[str, str],
    ) -> None:
        """Empty directory index has heading but no list items."""
        text = _render_dir_index([], "entity", slug_map)
        assert text.startswith("# Entities")
        assert "- " not in text


# ---------------------------------------------------------------------------
# export_bundle integration tests
# ---------------------------------------------------------------------------


class TestExportBundle:
    """Integration tests for the full export_bundle orchestrator.

    Seeds real Entity and WikiPage rows in the test database and verifies
    the bundle structure written through a tmp_path-rooted LocalStorageProvider.
    """

    async def test_full_bundle_creation(
        self,
        db: AsyncSession,
        tmp_path: Path,
    ) -> None:
        """First export creates a complete bundle with all expected files."""
        eid = uuid.UUID("11111111-1111-1111-1111-111111111111")
        entity = Entity(
            id=eid,
            name="Acme Corp",
            entity_type="organization",
            description="A fictional corporation.",
        )
        db.add(entity)
        db.add(
            WikiPage(
                entity_id=eid,
                title="Acme Corp",
                slug="acme-corp-11111111",
                content=(
                    "**Acme Corp** is a fictional organization.\n\n"
                    "See [[other-entity]]."
                ),
                status=PublishedStatus.PUBLISHED,
                synthesized_at=datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC),
            )
        )
        # Source-summary page
        db.add(
            WikiPage(
                entity_id=None,
                title="Meeting Notes",
                slug="meeting-notes-22222222",
                content="Discussed project roadmap.\n\nKey decisions recorded.",
                status=PublishedStatus.PUBLISHED,
                synthesized_at=datetime(2026, 6, 16, 14, 30, 0, tzinfo=UTC),
                synthesized_from_sources=[
                    "22222222-2222-2222-2222-222222222222",
                ],
            )
        )
        await db.flush()

        settings_mock = MagicMock()
        settings_mock.upload_dir = tmp_path / "uploads"
        storage = LocalStorageProvider(settings_mock)

        count = await export_bundle(db, storage, tmp_path, "http://localhost:8000")

        assert count == 2

        # Entity page
        entity_file = tmp_path / "entities" / "acme-corp-11111111.md"
        assert entity_file.is_file()
        entity_content = entity_file.read_text()
        assert entity_content.startswith("---")
        assert "type: organization" in entity_content
        assert "[[other-entity]]" not in entity_content
        assert "[other-entity](../entities/other-entity.md)" in entity_content

        # Source page
        source_file = tmp_path / "sources" / "meeting-notes-22222222.md"
        assert source_file.is_file()

        # Root index
        root_index = tmp_path / "index.md"
        assert root_index.is_file()
        root_text = root_index.read_text()
        assert "## Entities" in root_text
        assert "## Source Summaries" in root_text
        assert "Acme Corp" in root_text
        assert "Meeting Notes" in root_text

        # Entity index
        entity_index = tmp_path / "entities" / "index.md"
        assert entity_index.is_file()
        entity_index_text = entity_index.read_text()
        assert "Acme Corp" in entity_index_text

        # Source index
        source_index = tmp_path / "sources" / "index.md"
        assert source_index.is_file()
        source_index_text = source_index.read_text()
        assert "Meeting Notes" in source_index_text

        # Manifest
        manifest_file = tmp_path / ".rag-wiki-export-manifest.json"
        assert manifest_file.is_file()
        manifest_data = json.loads(manifest_file.read_text())
        assert "entities/acme-corp-11111111.md" in manifest_data
        assert "sources/meeting-notes-22222222.md" in manifest_data
        assert len(manifest_data["entities/acme-corp-11111111.md"]) == 64

        # Log
        log_file = tmp_path / "log.md"
        assert log_file.is_file()
        log_text = log_file.read_text()
        assert "**added**" in log_text

    async def test_rerun_no_changes(
        self,
        db: AsyncSession,
        tmp_path: Path,
    ) -> None:
        """Re-exporting with identical data produces no new log entries."""
        eid = uuid.UUID("11111111-1111-1111-1111-111111111111")
        entity = Entity(
            id=eid,
            name="Acme Corp",
            entity_type="organization",
        )
        db.add(entity)
        db.add(
            WikiPage(
                entity_id=eid,
                title="Acme Corp",
                slug="acme-corp-11111111",
                content="Content.",
                status=PublishedStatus.PUBLISHED,
            )
        )
        await db.flush()

        settings_mock = MagicMock()
        settings_mock.upload_dir = tmp_path / "uploads"
        storage = LocalStorageProvider(settings_mock)

        count1 = await export_bundle(db, storage, tmp_path, "http://localhost:8000")
        assert count1 == 1

        count2 = await export_bundle(db, storage, tmp_path, "http://localhost:8000")
        assert count2 == 0

        log_text = (tmp_path / "log.md").read_text()
        assert log_text.count("**added**") == 1

    async def test_orphan_deletion(
        self,
        db: AsyncSession,
        tmp_path: Path,
    ) -> None:
        """Pages removed from the DB are deleted from the bundle on re-export."""
        eid = uuid.UUID("11111111-1111-1111-1111-111111111111")
        entity = Entity(
            id=eid,
            name="Acme Corp",
            entity_type="organization",
        )
        db.add(entity)
        db.add(
            WikiPage(
                entity_id=eid,
                title="Acme Corp",
                slug="acme-corp-11111111",
                content="Content.",
                status=PublishedStatus.PUBLISHED,
            )
        )
        await db.flush()

        settings_mock = MagicMock()
        settings_mock.upload_dir = tmp_path / "uploads"
        storage = LocalStorageProvider(settings_mock)

        await export_bundle(db, storage, tmp_path, "http://localhost:8000")

        # Delete the page
        await db.execute(sa.delete(WikiPage))
        await db.flush()

        await export_bundle(db, storage, tmp_path, "http://localhost:8000")

        entity_file = tmp_path / "entities" / "acme-corp-11111111.md"
        assert not entity_file.exists()

        log_text = (tmp_path / "log.md").read_text()
        assert "**removed**" in log_text

    async def test_empty_database(
        self,
        db: AsyncSession,
        tmp_path: Path,
    ) -> None:
        """Export with no published pages still writes all index files."""
        settings_mock = MagicMock()
        settings_mock.upload_dir = tmp_path / "uploads"
        storage = LocalStorageProvider(settings_mock)

        count = await export_bundle(db, storage, tmp_path, "http://localhost:8000")
        assert count == 0

        root_index = tmp_path / "index.md"
        assert root_index.is_file()
        assert "## Entities" not in root_index.read_text()

        entity_index = tmp_path / "entities" / "index.md"
        assert entity_index.is_file()
        assert entity_index.read_text().startswith("# Entities")

        source_index = tmp_path / "sources" / "index.md"
        assert source_index.is_file()
        assert source_index.read_text().startswith("# Source Summaries")

    async def test_manifest_persistence(
        self,
        db: AsyncSession,
        tmp_path: Path,
    ) -> None:
        """Manifest persists content hashes across export runs."""
        eid = uuid.UUID("11111111-1111-1111-1111-111111111111")
        entity = Entity(
            id=eid,
            name="Acme Corp",
            entity_type="organization",
        )
        db.add(entity)
        page = WikiPage(
            entity_id=eid,
            title="Acme Corp",
            slug="acme-corp-11111111",
            content="Original content.",
            status=PublishedStatus.PUBLISHED,
        )
        db.add(page)
        await db.flush()

        settings_mock = MagicMock()
        settings_mock.upload_dir = tmp_path / "uploads"
        storage = LocalStorageProvider(settings_mock)

        await export_bundle(db, storage, tmp_path, "http://localhost:8000")

        # Read manifest
        manifest_data = json.loads(
            (tmp_path / ".rag-wiki-export-manifest.json").read_text()
        )
        first_hash = manifest_data["entities/acme-corp-11111111.md"]

        # Update content and re-export
        page.content = "Updated content."
        await db.flush()

        await export_bundle(db, storage, tmp_path, "http://localhost:8000")

        manifest_data = json.loads(
            (tmp_path / ".rag-wiki-export-manifest.json").read_text()
        )
        second_hash = manifest_data["entities/acme-corp-11111111.md"]
        assert second_hash != first_hash
