"""
rag_wiki.wiki.export
--------------------
Pure functions for building OKF export bundles from wiki pages.

Provides slug-to-name map construction (from DB) and inline [[slug]]→Markdown
link rewriting. Both functions are the core render step: read Postgres, rewrite
links, never write back. Does NOT handle file I/O, manifest management, or CLI
orchestration — those live in the caller (``rag-wiki export`` CLI command).

See ADR-0019 for the OKF export format specification.
"""

from __future__ import annotations

import re
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from rag_wiki.db.models.source import Source
from rag_wiki.db.models.wiki import WikiPage

logger = structlog.get_logger(__name__)

# Match [[slug]] — captures the slug portion inside the brackets.
# Uses a negative lookbehind for a backtick to avoid matching inside inline
# code spans. This is a best-effort guard; deeply nested or fenced code blocks
# may still produce false positives.
_SLUG_LINK_RE = re.compile(r"(?<!`)\[\[([^\]]+)\]\]")


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
