"""rag_wiki.db.models.index
-----------------------
Canonical re-export point for all SQLAlchemy models.

Import from here (or ``rag_wiki.db.models``) to reference any model. This
module is the single source of truth for "which models exist."
"""

from rag_wiki.db.models.graph import Entity, PublishedStatus, Relation
from rag_wiki.db.models.jobs import Job, JobStatus
from rag_wiki.db.models.source import Chunk, ChunkEntity, ProcessingStatus, Source
from rag_wiki.db.models.wiki import WikiPage, WikiPageEntity

__all__ = [
    "Chunk",
    "ChunkEntity",
    "Entity",
    "Job",
    "JobStatus",
    "ProcessingStatus",
    "PublishedStatus",
    "Relation",
    "Source",
    "WikiPage",
    "WikiPageEntity",
]
