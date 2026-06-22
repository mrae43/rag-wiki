"""Tests for rag_wiki.wiki.prompts — Jinja2 template rendering."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

_TEMPLATE_DIR = (
    Path(__file__).resolve().parent.parent.parent / "rag_wiki" / "prompts" / "templates"
)
_jinja_env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)))


def test_synthesize_entity_renders_with_minimal_context() -> None:
    """Entity template renders without errors given minimal context."""
    template = _jinja_env.get_template("synthesize_entity.j2")
    result = template.render(
        entity_name="TestEntity",
        entity_type="concept",
        entity_description="A test",
        existing_page=None,
        edges=[],
        source_chunks=[],
        known_entities=[],
    )
    assert "# TestEntity" in result
    assert "create a new wiki page" in result


def test_synthesize_entity_renders_with_existing_page() -> None:
    """Entity template renders update variant when existing_page is provided."""
    template = _jinja_env.get_template("synthesize_entity.j2")
    result = template.render(
        entity_name="TestEntity",
        entity_type="concept",
        entity_description="A test",
        existing_page="Some existing content",
        edges=[{"label": "knows", "target_slug": "bob-1234"}],
        source_chunks=[
            {
                "source_file": "doc.txt",
                "index": 0,
                "source_name": "doc.txt",
                "ingested_at": "",
                "text": "Some content",
            }
        ],
        known_entities=[{"slug": "bob-1234", "name": "Bob"}],
    )
    assert "# TestEntity" in result
    assert "update an existing wiki page" in result
    assert "Some existing content" in result
    assert "knows" in result
    assert "[[bob-1234]]" in result
    assert "Some content" in result


def test_synthesize_entity_handles_empty_lists() -> None:
    """Entity template handles empty edges, source_chunks, and known_entities."""
    template = _jinja_env.get_template("synthesize_entity.j2")
    result = template.render(
        entity_name="E",
        entity_type="concept",
        entity_description="",
        existing_page=None,
        edges=[],
        source_chunks=[],
        known_entities=[],
    )
    assert "# E" in result
    assert "Relationships" in result


def test_synthesize_entity_indentation_preserved() -> None:
    """Jinja2 trim_blocks/lstrip_blocks doesn't break markdown indentation."""
    template = _jinja_env.get_template("synthesize_entity.j2")
    result = template.render(
        entity_name="E",
        entity_type="concept",
        entity_description="",
        existing_page=None,
        edges=[],
        source_chunks=[],
        known_entities=[],
    )
    assert "1. **Chunks only.**" in result
    assert "2. **No citation fabrication.**" in result


def test_synthesize_source_summary_renders_with_minimal_context() -> None:
    """Source summary template renders without errors."""
    template = _jinja_env.get_template("synthesize_source_summary.j2")
    result = template.render(
        source_file_name="doc.txt",
        ingested_at="2024-01-01T00:00:00",
        chunk_count=0,
        chunks=[],
        touched_entities=[],
        source_relations=[],
        reingest_count=0,
        previous_ingested_at=None,
    )
    assert "# doc.txt" in result
    assert "You are a knowledge wiki curator" in result


def test_synthesize_source_summary_with_entities_and_relations() -> None:
    """Source summary renders with entities and relations."""
    template = _jinja_env.get_template("synthesize_source_summary.j2")
    result = template.render(
        source_file_name="doc.txt",
        ingested_at="2024-01-01T00:00:00",
        chunk_count=1,
        chunks=[
            {
                "source_file": "doc.txt",
                "index": 0,
                "text": "Hello world",
                "summary_or_first_line": "Hello world",
            }
        ],
        touched_entities=[{"slug": "alice-1234", "name": "Alice"}],
        source_relations=[
            {
                "source_slug": "alice-1234",
                "label": "knows",
                "target_slug": "bob-5678",
            }
        ],
        reingest_count=0,
        previous_ingested_at=None,
    )
    assert "# doc.txt" in result
    assert "Alice" in result
    assert "knows" in result
    assert "[[alice-1234]]" in result
