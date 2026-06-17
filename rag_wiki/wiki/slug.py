"""
rag_wiki.wiki.slug
------------------
Deterministic slug generation for wiki pages.

Collisions are avoided by appending the first 8 characters of the entity UUID.
Does NOT query the database for existing slugs — uniqueness is guaranteed by
the UUID suffix.
"""

from __future__ import annotations

import re
import uuid

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def generate_slug(name: str, entity_id: uuid.UUID) -> str:
    """Return a URL-safe slug for a wiki page.

    The slug is derived from the entity name plus a short UUID suffix, making
    it deterministic and effectively collision-free without a DB round-trip.

    Args:
        name: The canonical name of the entity (or source title).
        entity_id: The UUID of the entity. The first 8 hex characters are
            appended to the slug.

    Returns:
        A lowercase, hyphen-delimited slug ending with ``-<uuid_prefix>``.

    Example:
        >>> generate_slug(
        ...     "Acme Corp", uuid.UUID("12345678-1234-5678-1234-567812345678")
        ... )
        'acme-corp-12345678'
    """
    normalized = name.lower().strip()
    slugified = _SLUG_RE.sub("-", normalized).strip("-")
    return f"{slugified}-{str(entity_id)[:8]}"
