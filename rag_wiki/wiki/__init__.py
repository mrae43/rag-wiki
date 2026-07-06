"""
rag_wiki.wiki
------------
Wiki page synthesis and export.

Generates LLM-maintained markdown pages from the knowledge graph and provides
optional export to local files (e.g., for Obsidian).
"""

from rag_wiki.wiki.export import build_slug_name_map, rewrite_links
from rag_wiki.wiki.synthesis import (
    JOB_TYPE_SYNTHESIZE_ENTITY,
    JOB_TYPE_SYNTHESIZE_SOURCE_SUMMARY,
    synthesize_entity_page,
    synthesize_source_summary,
)

__all__ = [
    "build_slug_name_map",
    "JOB_TYPE_SYNTHESIZE_ENTITY",
    "JOB_TYPE_SYNTHESIZE_SOURCE_SUMMARY",
    "rewrite_links",
    "synthesize_entity_page",
    "synthesize_source_summary",
]
