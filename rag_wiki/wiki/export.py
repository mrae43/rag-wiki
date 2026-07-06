"""
rag_wiki.wiki.export
--------------------
OKF export bundle builder: renders wiki pages to OKF markdown, manages the
hidden manifest, appends to log.md, and deletes orphaned page files.

See ADR-0019 for the OKF export format specification.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from rag_wiki.db.models.source import Source
from rag_wiki.db.models.wiki import WikiPage
from rag_wiki.exceptions import StorageError
from rag_wiki.storage.base import StorageProvider

logger = structlog.get_logger(__name__)

# Match [[slug]] — captures the slug portion inside the brackets.
# Uses a negative lookbehind for a backtick to avoid matching inside inline
# code spans. This is a best-effort guard; deeply nested or fenced code blocks
# may still produce false positives.
_SLUG_LINK_RE = re.compile(r"(?<!`)\[\[([^\]]+)\]\]")

# Manifest filename (hidden dotfile, not an OKF concept).
_MANIFEST_FILENAME = ".rag-wiki-export-manifest.json"

# Log filename (OKF reserved navigation aid).
_LOG_FILENAME = "log.md"


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------


def _hash_content(content: str) -> str:
    """Return a SHA-256 hex digest of the given content string.

    Args:
        content: The content to hash.

    Returns:
        A 64-character hex string.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Excerpt helpers
# ---------------------------------------------------------------------------


def _first_paragraph_excerpt(content: str, max_chars: int = 200) -> str:
    """Extract the first paragraph of content as an excerpt.

    Takes the first paragraph (text before the first blank line), truncates
    it to ``max_chars`` at a word boundary, and appends an ellipsis if
    truncated.

    Args:
        content: The full markdown content.
        max_chars: Maximum character count for the excerpt. Defaults to 200.

    Returns:
        An excerpt string, never empty (falls back to ``content[:max_chars]``
        if no paragraph break is found).
    """
    para_end = content.find("\n\n")
    para = content[:para_end] if para_end != -1 else content
    para = para.strip()

    if len(para) <= max_chars:
        return para

    truncated = para[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated + "..."


# ---------------------------------------------------------------------------
# Front-matter builder
# ---------------------------------------------------------------------------


def _build_front_matter(
    page: WikiPage,
    slug_map: dict[str, str],
    api_base_url: str,
) -> str:
    """Build OKF YAML front-matter for a wiki page.

    Produces a six-field front-matter block per ADR-0019 §2.

    Args:
        page: The wiki page to generate front-matter for.
        slug_map: Slug-to-display-name map (for source-summary source lookup).
        api_base_url: Base URL for the ``resource`` field.

    Returns:
        A YAML front-matter string delimited by ``---``.
    """
    lines = ["---"]

    if page.entity_id is not None and page.entity is not None:
        entity_type = page.entity.entity_type or "unknown"
        description = page.entity.description
        resource = f"{api_base_url.rstrip('/')}/entities/{page.entity.id}"
        page_kind = "entity"
    else:
        entity_type = "rag-wiki:source-summary"
        description = _first_paragraph_excerpt(page.content)
        if page.synthesized_from_sources:
            first = page.synthesized_from_sources[0]
            resource = f"{api_base_url.rstrip('/')}/sources/{first}"
        else:
            resource = None
        page_kind = "source_summary"

    _append_yaml_field(lines, "type", entity_type)
    _append_yaml_field(lines, "title", page.title)

    if description:
        _append_yaml_field(lines, "description", description)

    timestamp = None
    if page.synthesized_at is not None:
        if isinstance(page.synthesized_at, datetime):
            timestamp = page.synthesized_at.isoformat()
        else:
            timestamp = str(page.synthesized_at)
    if timestamp:
        _append_yaml_field(lines, "timestamp", timestamp)

    _append_yaml_field(lines, "page_kind", page_kind)

    if resource:
        _append_yaml_field(lines, "resource", resource)

    lines.append("---")
    return "\n".join(lines)


def _append_yaml_field(lines: list[str], key: str, value: str) -> None:
    """Append a YAML key-value pair to the field list, quoting if necessary.

    Args:
        lines: List of YAML lines being built.
        key: The field name.
        value: The field value.
    """
    needs_quoting = any(
        c in value
        for c in (":", "#", "{", "}", "[", "]", ",", ">", "|", '"', "'", "%", "@", "`")
    )
    if needs_quoting:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key}: "{escaped}"')
    else:
        lines.append(f"{key}: {value}")


# ---------------------------------------------------------------------------
# Page path helpers
# ---------------------------------------------------------------------------


def _page_kind(page: WikiPage) -> str:
    """Return the page kind for a wiki page.

    Args:
        page: The wiki page.

    Returns:
        ``"entity"`` if the page has an associated entity,
        ``"source_summary"`` otherwise.
    """
    return "entity" if page.entity_id is not None else "source_summary"


def _page_path(slug: str, kind: str) -> str:
    """Return the OKF bundle path for a page slug.

    Args:
        slug: The page slug.
        kind: ``"entity"`` or ``"source_summary"``.

    Returns:
        A relative path like ``entities/{slug}.md``.
    """
    prefix = "entities" if kind == "entity" else "sources"
    return f"{prefix}/{slug}.md"


# ---------------------------------------------------------------------------
# Page renderer
# ---------------------------------------------------------------------------


def _render_page(
    page: WikiPage,
    slug_map: dict[str, str],
    api_base_url: str,
) -> tuple[str, str, str]:
    """Render a wiki page into OKF format.

    Produces the rendered markdown (front-matter + rewritten body), the
    content hash, and the bundle path.

    Args:
        page: The wiki page to render.
        slug_map: Slug-to-display-name map for link rewriting.
        api_base_url: Base URL for front-matter ``resource`` fields.

    Returns:
        A tuple of ``(path, rendered_content, content_hash)``.
    """
    kind = _page_kind(page)
    front_matter = _build_front_matter(page, slug_map, api_base_url)
    body = rewrite_links(page.content, slug_map)
    rendered = f"{front_matter}\n\n{body}\n"
    content_hash = _hash_content(rendered)
    path = _page_path(page.slug, kind)
    return path, rendered, content_hash


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


@dataclass
class _Manifest:
    """Hidden scratch-state manifest for the OKF export bundle.

    Stores a mapping of bundle file paths (e.g. ``entities/slug.md``) to
    SHA-256 content hashes from the last successful export. Used to detect
    added, modified, and removed pages.

    The manifest is persisted at ``{root_dir}/.rag-wiki-export-manifest.json``
    and is not an OKF concept — consumers ignore it.

    Example:
        .. code-block:: json

            {
              "entities/acme.md": "abc123...",
              "sources/logbook.md": "def456..."
            }
    """

    _data: dict[str, str] = field(default_factory=dict)
    path: str = _MANIFEST_FILENAME

    @classmethod
    async def load(
        cls,
        storage: StorageProvider,
        root_dir: Path,
    ) -> _Manifest:
        """Load the manifest from storage.

        If the manifest does not exist or is corrupt, returns an empty
        manifest (equivalent to a first-time export).

        Args:
            storage: The storage provider.
            root_dir: The bundle root directory (passed to ``read_text``).

        Returns:
            A populated ``_Manifest`` instance.
        """
        manifest = cls()
        try:
            raw = await storage.read_text(manifest.path, root_dir=root_dir)
            data = json.loads(raw)
            if isinstance(data, dict):
                manifest._data = {str(k): str(v) for k, v in data.items()}
        except StorageError:
            logger.info("No existing manifest found, starting fresh")
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Corrupt manifest, starting fresh", error=str(exc))
        return manifest

    async def save(self, storage: StorageProvider, root_dir: Path) -> None:
        """Persist the manifest to storage.

        Args:
            storage: The storage provider.
            root_dir: The bundle root directory (passed to ``write_text``).
        """
        raw = json.dumps(self._data, indent=2, sort_keys=True)
        await storage.write_text(self.path, raw, root_dir=root_dir)

    def get(self, path: str) -> str | None:
        """Return the stored hash for a bundle path, or ``None``.

        Args:
            path: A bundle path like ``entities/slug.md``.

        Returns:
            The stored content hash, or ``None`` if not tracked.
        """
        return self._data.get(path)

    def __setitem__(self, path: str, hash_value: str) -> None:
        self._data[path] = hash_value

    def __getitem__(self, path: str) -> str:
        return self._data[path]

    def __contains__(self, path: str) -> bool:
        return path in self._data

    def removed_vs(self, new_state: dict[str, str]) -> set[str]:
        """Return paths present in this manifest but absent from ``new_state``.

        Args:
            new_state: The state dict from a completed export run mapping
                bundle paths to content hashes.

        Returns:
            A set of bundle paths that were in this manifest but are not in
            ``new_state`` (i.e., pages that were removed from the DB since the
            last export).
        """
        current = set(self._data.keys())
        new = set(new_state.keys())
        return current - new


# ---------------------------------------------------------------------------
# Log writer
# ---------------------------------------------------------------------------


class _LogWriter:
    """Append-only changelog writer for ``log.md``.

    Accumulates log entries in memory and flushes them to storage at the
    end of the export run. Each entry records an added, modified, or removed
    page.

    The log is written to ``{root_dir}/log.md`` (OKF reserved navigation aid).
    """

    def __init__(self, storage: StorageProvider, root_dir: Path) -> None:
        """Initialize the log writer.

        Args:
            storage: The storage provider.
            root_dir: The bundle root directory (passed to ``read_text`` and
                ``write_text``).
        """
        self._storage = storage
        self._root_dir = root_dir
        self._entries: list[str] = []

    def add_entry(
        self,
        action: str,
        path: str,
        title: str,
    ) -> None:
        """Queue a log entry for the next flush.

        Args:
            action: One of ``"added"``, ``"modified"``, or ``"removed"``.
            path: The bundle path (e.g. ``entities/slug.md``).
            title: The page title.
        """
        self._entries.append(f"- **{action}** `{path}` — {title}")

    async def flush(self) -> None:
        """Flush all accumulated entries to ``log.md`` in storage.

        Reads the existing log (if any), appends new entries under a
        timestamp heading, and writes the result back. Idempotent: clears
        the internal buffer so a second flush is a no-op.
        """
        if not self._entries:
            return

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")  # noqa: UP017
        existing = ""
        try:
            existing = await self._storage.read_text(
                _LOG_FILENAME, root_dir=self._root_dir
            )
            if existing and not existing.endswith("\n"):
                existing += "\n"
        except StorageError:
            pass

        block = f"\n## {now}\n\n" + "\n".join(self._entries) + "\n"
        updated = existing + block
        await self._storage.write_text(_LOG_FILENAME, updated, root_dir=self._root_dir)
        self._entries.clear()


# ---------------------------------------------------------------------------
# Orphan deletion
# ---------------------------------------------------------------------------


async def _delete_orphan_pages(
    removed_paths: set[str],
    storage: StorageProvider,
    root_dir: Path,
) -> None:
    """Delete orphan page files from the export bundle.

    Deletes every file path in ``removed_paths`` from storage. Non-existent
    files are silently skipped (the file may have been on a different
    storage provider configuration from a prior run).

    Args:
        removed_paths: Set of relative bundle paths to delete (e.g.
            ``{"entities/acme.md", "sources/logbook.md"}``).
        storage: The storage provider.
        root_dir: The bundle root directory passed to ``delete``.
    """
    for path in sorted(removed_paths):
        try:
            await storage.delete(path, root_dir=root_dir)
        except StorageError:
            logger.debug("Orphan file not found, skipping", path=path)


# ---------------------------------------------------------------------------
# Public API — kept from earlier refactoring for backward compatibility.
# ---------------------------------------------------------------------------


async def build_slug_name_map(db: AsyncSession) -> dict[str, str]:
    """
    Build a map from wiki page slug to display name for all published pages.

    Entity pages: display name = ``entity.name``.
    Source-summary pages: display name = ``source.file_name`` (resolved from
    the first source ID in ``synthesized_from_sources``). If the source cannot
    be resolved, the slug itself is used as the display name.

    The map covers every published wiki page so that ``rewrite_links`` can
    resolve any ``[[slug]]`` reference, including forward links to pages that
    have not yet been synthesised (the slug itself becomes the label).

    Args:
        db: An active async database session.

    Returns:
        A dict mapping ``slug`` → ``display_name``. Every published page slug
        appears as a key; unresolvable entries use the slug as their own label.
    """
    result = await db.execute(
        select(WikiPage)
        .options(joinedload(WikiPage.entity))
        .where(WikiPage.status == "published")
    )
    pages = result.unique().scalars().all()

    slug_map: dict[str, str] = {}
    slug_to_source_id: dict[str, uuid.UUID] = {}

    for page in pages:
        if page.entity_id is not None and page.entity is not None:
            slug_map[page.slug] = page.entity.name
        elif page.synthesized_from_sources:
            first = page.synthesized_from_sources[0]
            try:
                slug_to_source_id[page.slug] = uuid.UUID(first)
            except (ValueError, TypeError):
                slug_map[page.slug] = page.slug
        else:
            slug_map[page.slug] = page.slug

    if slug_to_source_id:
        unique_ids = set(slug_to_source_id.values())
        source_rows = await db.execute(select(Source).where(Source.id.in_(unique_ids)))
        source_name_map = {s.id: s.file_name for s in source_rows.scalars().all()}
        for slug, sid in slug_to_source_id.items():
            slug_map[slug] = source_name_map.get(sid, slug)

    return slug_map


def rewrite_links(content: str, slug_map: dict[str, str]) -> str:
    """
    Rewrite internal ``[[slug]]`` Obsidian-style wiki links to OKF Markdown
    links.

    ``[[some-slug]]`` becomes ``[Display Name](../entities/some-slug.md)``.
    The display name is looked up in ``slug_map``; if the slug is not in the
    map (a forward reference to a not-yet-written page), the slug itself is
    used as the label.

    Args:
        content: Wiki page body text (may contain zero or more ``[[slug]]``
            references).
        slug_map: A precomputed mapping from slug to display name (see
            ``build_slug_name_map``).

    Returns:
        The content with all ``[[slug]]`` references rewritten to OKF
        Markdown links.
    """

    def _replace(match: re.Match[str]) -> str:
        slug = match.group(1)
        label = slug_map.get(slug, slug)
        return f"[{label}](../entities/{slug}.md)"

    return _SLUG_LINK_RE.sub(_replace, content)
