"""Tests for rag_wiki.wiki.slug."""

from __future__ import annotations

import uuid

from rag_wiki.wiki.slug import generate_slug


def test_generate_slug_basic() -> None:
    """Slugify a simple name and append the UUID prefix."""
    entity_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    assert generate_slug("Acme Corp", entity_id) == "acme-corp-12345678"


def test_generate_slug_lowercases() -> None:
    """Mixed-case names are normalised to lowercase."""
    entity_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    assert generate_slug("NASA", entity_id) == "nasa-aaaaaaaa"


def test_generate_slug_strips_punctuation() -> None:
    """Punctuation is replaced with hyphens and stripped from edges."""
    entity_id = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    assert generate_slug("C++ / Python", entity_id) == "c-python-bbbbbbbb"


def test_generate_slug_collapses_multiple_separators() -> None:
    """Consecutive non-alphanumeric characters collapse to a single hyphen."""
    entity_id = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
    assert generate_slug("Hello   World!!", entity_id) == "hello-world-cccccccc"


def test_generate_slug_strips_leading_trailing_separators() -> None:
    """Leading and trailing non-alphanumeric characters are removed."""
    entity_id = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    assert generate_slug("---Leading---", entity_id) == "leading-dddddddd"


def test_generate_slug_deterministic() -> None:
    """Calling with the same inputs always yields the same output."""
    entity_id = uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
    assert generate_slug("Foo Bar", entity_id) == generate_slug("Foo Bar", entity_id)


def test_generate_slug_unique_per_entity() -> None:
    """Different UUIDs for the same name produce different slugs."""
    name = "Same Name"
    slug_a = generate_slug(name, uuid.UUID("11111111-1111-1111-1111-111111111111"))
    slug_b = generate_slug(name, uuid.UUID("22222222-2222-2222-2222-222222222222"))
    assert slug_a != slug_b
