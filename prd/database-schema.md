# PRD: Database Schema Design for LLM Wiki Knowledge Graph

## Problem Statement

The project is a self-hosted system that builds an LLM-maintained knowledge wiki from documents over a Postgres + knowledge graph backend. The codebase is currently in early stages with only the SQLAlchemy `Base`/`TimestampMixin`/`UUIDMixin` base classes and a single migration (adding the pgvector extension). No model files or database tables exist yet. The schema needs to be designed and implemented before any ingestion, graph, or wiki functionality can be built.

## Solution

Design and implement a complete SQLAlchemy model + Alembic migration for the database schema covering all domain objects defined in the project's ADRs: Sources, Chunks, Entities, Relations, Wiki Pages, and Jobs. The schema supports the full ingestion pipeline, knowledge graph, and wiki synthesis as outlined in the architecture decisions.

## User Stories

1. As a developer, I want `sources` and `chunks` tables with proper metadata and embedding columns, so that the ingestion pipeline can store documents and their extracted atomic units.
2. As a developer, I want `entities` and `relations` tables with embeddings and provenance, so that the knowledge graph can be stored and traversed using recursive CTEs.
3. As a developer, I want a `chunk_entities` join table linking chunks to entities, so that we can track which entities appear in which chunks.
4. As a developer, I want `wiki_pages` stored in Postgres with ownership and mentions relationships, so that the wiki can be persisted and updated.
5. As a developer, I want a `wiki_page_entities` join table for backlink traversal, so that wiki pages can reference multiple entities.
6. As a developer, I want a `jobs` table with retry logic, so that the Postgres-native job queue can handle ingest, lint, and export jobs.
7. As a developer, I want separate status vocabularies per table, so that each domain object's lifecycle is accurately modeled.

## Implementation Decisions

### Schema Design

The schema consists of 9 tables:

- **`sources`**: Metadata about ingested documents. File content stored on disk; metadata stored in Postgres.
- **`chunks`**: Atomic units extracted from sources, with text content and embedding.
- **`entities`**: Knowledge graph nodes with canonical name, type, description, and embedding.
- **`relations`**: Knowledge graph edges linking source and target entities, with provenance via `chunk_id`.
- **`chunk_entities`**: Join table linking chunks to entities (many-to-many).
- **`wiki_pages`**: LLM-maintained markdown pages with title, slug, content, and ownership via `entity_id`.
- **`wiki_page_entities`**: Join table for backlink traversal (many-to-many: wiki pages mention entities).
- **`jobs`**: Postgres-native job queue with retry logic and status tracking.

### Status Vocabularies

Separate status vocabularies per table:

- **Processing status** (`sources`, `chunks`): `pending` → `processing` → `processed` / `failed`
- **Published status** (`entities`, `relations`, `wiki_pages`): `published` (v1 only; future adds `pending_review`)
- **Job status** (`jobs`): `pending` → `processing` → `completed` / `failed`

### Key Decisions

- **File storage on disk**: Source file content is stored on the filesystem, with the path stored in `sources.file_path`. Postgres stores only metadata.
- **Join table for chunk↔entity links**: A `chunk_entities` table is used instead of embedding chunk references in relations. This avoids the complexity of finding entities that have no relation edges yet.
- **Two relationships between wiki pages and entities**: `wiki_pages.entity_id` (ownership, nullable, unique) models the canonical page for an entity; `wiki_page_entities` (mentions) models backlink traversal.
- **Free-text entity types**: `entities.entity_type` is a free-text field, allowing the LLM to invent new types dynamically.
- **Wiki page revisions skipped for v1**: No `wiki_page_revisions` table per ADR-0006. Revisions deferred to a future ADR.
- **Jobs status lifecycle simplified**: `claimed` and `processing` collapsed into a single `processing` state, since the `SKIP LOCKED` claim immediately starts work.

### Indexing Strategy

- `chunks.embedding`: pgvector HNSW index (N from settings)
- `entities.embedding`: pgvector index for entity resolution
- `relations.source_entity_id`, `relations.target_entity_id`, `relations.relation_type`: composite index for graph traversal
- `chunks.source_id`, `chunks.chunk_index`: composite index for ordered retrieval
- `wiki_pages.slug`: unique index for URL-safe identifiers
- `jobs.status`, `jobs.scheduled_at`: composite index for efficient claiming

## Testing Decisions

- All model files will be tested via Alembic migration tests (ensure migration creates tables with correct columns)
- Unit tests for model validation and foreign key relationships
- FakeLLMProvider used in tests for extraction-related paths (per coding standards)
- Database setup/teardown via pytest-asyncio with asyncpg for integration tests

## Out of Scope

- Wiki page revisions (`wiki_page_revisions` table) — deferred to v2
- User auth / RBAC (flagged in ADR-0004 as not yet decided)
- Observability schema (structured logging metrics tables) — deferred
- Elasticsearch / full-text search indexes — pgvector covers v1 needs

## Further Notes

- The schema is designed to be forward-compatible with future ADR decisions (e.g. `pending_review` status for entities/wiki_pages, `scheduled_at` for jobs)
- Migration must be generated via `alembic revision --autogenerate` after models are written
- All schema changes must go through Alembic (per coding standards)
