# PRD: Entity/Relation Extraction + Real-time Resolution

## Problem Statement

The ingestion pipeline (parse → chunk → embed) produces Chunks with vector embeddings, but the knowledge graph (`entities` and `relations` tables) remains empty. The system cannot extract real-world concepts from chunk text, link them to the source Chunks, resolve duplicates across documents, or build the graph that powers retrieval (ADR-0009) and wiki synthesis (ADR-0006). Without extraction and resolution, the Postgres knowledge graph is a schema with no data.

## Solution

Build an extraction and resolution subsystem under `rag_wiki/graph/` that:
1. Extracts entities and relations from each Chunk using an LLM tool call
2. Resolves newly extracted entities against existing graph entities using embedding similarity + an LLM merge decision
3. Persists the canonical graph and links it back to Chunks

This enables the full automated ingestion pipeline (ADR-0010): parse → chunk → extract → resolve → embed → wiki.

## User Stories

1. As a system admin, I want entities to be extracted automatically from each ingested chunk, so that the knowledge graph grows without manual data entry.
2. As a system admin, I want relations to be extracted alongside entities, so that the graph has edges, not just isolated nodes.
3. As a system admin, I want duplicate mentions of the same entity (e.g. "Apple Inc." and "Apple") to resolve into a single canonical entity, so the wiki does not fragment.
4. As a system admin, I want the resolution decision to be made by an LLM with context, so that ambiguous cases are handled with judgment, not brittle string matching.
5. As a developer, I want entity merge operations to be logged in a queryable table, so that post-hoc review and debugging are possible.
6. As a developer, I want the extraction and resolution code to be testable with a fake LLM provider, so that the test suite can run without API keys.
7. As a system admin, I want the extraction and resolution to use the same embedding model as the chunk pipeline, so that no additional embedding infrastructure is needed.
8. As a developer, I want the LLM tool-call schema to enforce valid JSON output, so that extraction parsing is deterministic and error-tolerant.
9. As a developer, I want the resolution step to be safe for concurrent ingestion jobs, so that duplicate entity creation is not possible even under parallel workers.
10. As a system admin, I want the merge process to hard-delete duplicate entities while re-pointing their relations, so that the graph remains clean without zombie records.
11. As a developer, I want the extraction and resolution modules to be domain-separated under `rag_wiki/graph/`, so that the graph layer can be reused by the lint pass, retrieval, and ad-hoc queries.
12. As a developer, I want the LLM model used for resolution to be configurable separately from the extraction model, so that the merge decision can use a stronger model without inflating the cost of extraction.
13. As a developer, I want the extraction step to be driven by the `ChatProvider` protocol (ADR-0007), so that no OpenAI/Anthropic SDK is imported directly.
14. As a developer, I want extraction and resolution errors to use the domain exception hierarchy (`EntityResolutionError`, `LLMProviderError`), so that error handling is consistent across the codebase.
15. As a developer, I want relations to reference extracted entities by positional index rather than surface form string, so that same-surface-form entities are unambiguously linked.
16. As a system admin, I want multiple chunks to independently corroborate the same relation, so that duplicate `(source, target, relation_type)` records are kept as evidence, not collapsed during merge.
17. As a developer, I want the extraction and resolution to be exposed as simple, single-purpose functions (`extract_entities`, `resolve_entities`), so that the ingest pipeline orchestration does not need to know internal prompt or vector search details.
18. As a developer, I want a similarity threshold to filter out obviously unrelated entity candidates before the LLM is called, so that API costs are controlled.

## Implementation Decisions

### Module structure

- **`rag_wiki/graph/schemas.py`** — Pydantic models for `ExtractedEntity`, `ExtractedRelation`, `ExtractionResult`, and `MergeDecision` (the LLM's structured output). These are the data shapes that cross the LLM boundary.
- **`rag_wiki/graph/extraction.py`** — Deep module. Single function `extract_entities(chunk: Chunk, provider: ChatProvider) -> ExtractionResult`. Encapsulates the prompt, the tool-call schema, and the LLM interaction. No database access.
- **`rag_wiki/graph/resolution.py`** — Deep module. Single function `resolve_entities(candidates: list[ExtractedEntity], chunk: Chunk, db: AsyncSession, provider: ChatProvider) -> list[Entity]` (or similar). Encapsulates: embed candidate → vector search → LLM merge decision per candidate → merge or create. Returns the canonical `Entity` records for the chunk, with the chunk-to-entity linking handled.
- **`rag_wiki/graph/merge.py`** — Shallow helper. Contains `merge_entity(from_id: UUID, into_id: UUID, db: AsyncSession)` and the audit-log write. Extracted so that `resolution.py` stays focused on the decision pipeline.

### Extraction output schema

```python
class ExtractedEntity(BaseModel):
    surface_form: str       # The text as it appeared in the chunk
    canonical_name: str     # The normalized entity name
    entity_type: str        # e.g. "person", "organization", "concept"
    description: str        # One-sentence summary

class ExtractedRelation(BaseModel):
    source_idx: int         # Index into ExtractionResult.entities
    target_idx: int         # Index into ExtractionResult.entities
    relation_type: str      # e.g. "CEO", "founded", "located_in"

class ExtractionResult(BaseModel):
    entities: list[ExtractedEntity]
    relations: list[ExtractedRelation]
```

### Relation linking

Relations are extracted alongside entities in a single LLM tool call. Because entities do not have DB IDs at extraction time, the LLM uses positional indices within the entity list to reference source and target. After resolution maps each `ExtractedEntity` to a canonical `Entity` (UUID), the relation is created with the resolved UUIDs.

### LLM extraction mechanism

The extraction prompt uses a tool-call schema via `ChatProvider.complete()` (which already supports `ToolDefinition` and `ToolCall` in the protocol). The provider is responsible for enforcing the JSON schema. This avoids parsing raw markdown output from a JSON-mode prompt.

### Embedding for similarity search

For each candidate entity, the system computes `embedding = embed(canonical_name + " " + description)` using the same embedding model and provider configured for chunk embeddings (`embedding_model`, `embedding_dimensions` from settings). The `entities` table already has an `embedding` column of type `Vector(embedding_dimensions)`.

### Resolution pipeline

1. Embed candidate `ExtractedEntity`
2. Vector search against `entities.embedding` with `distance <= distance_threshold` (default 0.6), limit `top_k` (default 5)
3. If no candidates, create new `Entity` with the embedding
4. If candidates exist, for each candidate in top-k:
   - Load the candidate's source chunk text
   - Present the new entity's record + chunk text + existing entity's record + chunk text to the LLM
   - Ask `merge_into_id` (UUID) or `decision: new`
   - On merge: call `merge_entity(from_id, into_id)` and use the surviving entity
   - On new: create new entity
5. Return the canonical `Entity` for each `ExtractedEntity`

### Merge mechanics

- `merge_entity` performs a hard delete of the absorbed entity (`B`) and re-points:
  - All `outgoing_relations` (source → target) → `source_entity_id` changes from `B` to `A`
  - All `incoming_relations` (source → target) → `target_entity_id` changes from `B` to `A`
  - All `chunk_entities` links → `entity_id` changes from `B` to `A`
  - All `wiki_pages.entity_id` → `SET NULL` or update to `A` (see schema: `entity_id` is `nullable` with `ondelete="SET NULL"`)
  - All `wiki_page_entities` join entries → `entity_id` changes from `B` to `A`
- Duplicate relations are **not** collapsed during merge. The `chunk_id` on each relation serves as provenance.
- Write a row to `entity_merge_log` with the from/to IDs, the chunk that triggered it, the job ID, and the LLM's reasoning.

### Concurrency

- `resolve_entities` acquires a Postgres advisory lock on the hash of the candidate's `canonical_name` before searching/creating. This serializes entity creation for a given name without locking the entire table.
- Chunks within a single source document are processed sequentially to avoid within-document races.
- The lock key is the 64-bit hash of `canonical_name` (or a `BIGINT` derived from the name). This is compatible with Postgres `pg_try_advisory_lock` and does not require a separate lock table.

### Error handling

- `LLMProviderError` (from provider) triggers retry per the worker's retry policy.
- After retries, an unresolvable entity raises `EntityResolutionError` and the job fails.
- If vector search returns no close candidates, this is a happy path (create new entity). No error.

### Schema addition

- Add `entity_merge_log` table (columns: `id`, `merged_from_id`, `merged_into_id`, `chunk_id`, `job_id`, `reason`, `created_at`). Index on `merged_into_id`.
- Add `llm_model_resolution` to settings (default: `gpt-4o`). This is an additive env var.

### Environment variables

- `LLM_MODEL_RESOLUTION` (default: `gpt-4o`) — the model for merge-vs-new decisions.
- `ENTITY_RESOLUTION_TOP_K` (default: 5) — max candidates for LLM review.
- `ENTITY_RESOLUTION_DISTANCE_THRESHOLD` (default: 0.6) — max distance for candidates to be considered.

## Testing Decisions

- **External behavior only**: tests verify that `extract_entities` returns a valid `ExtractionResult` given a `FakeChatProvider` returning a deterministic `CompletionResponse`. Tests do not verify prompt internals or JSON schema details.
- **Tested modules**: `extraction.py`, `resolution.py`, `merge.py`. The orchestration that calls these (e.g. `ingest_pipeline.py`) is tested separately.
- **Fake provider**: `FakeChatProvider` implements the `ChatProvider` protocol and returns canned `CompletionResponse` objects. This is consistent with the existing test pattern (`tests/providers/` test `FakeChatProvider` or similar mocks).
- **Error path tests**: at least one test per function for the failure case (e.g., `test_extract_entities_raises_on_provider_error`, `test_resolve_entity_creates_new_when_no_candidates`).
- **Integration test**: `test_extraction_resolution_roundtrip` with a real `FakeChatProvider` that handles both extraction and resolution steps, verifying the end-to-end `Chunk` → `ExtractionResult` → `Entity` flow.

## Out of Scope

- The periodic lint pass (ADR-0008) is a separate subsystem. This PRD covers only real-time resolution.
- Wiki page synthesis (updating `wiki_pages` after extraction) is out of scope — it will be handled in a separate PRD for `rag_wiki/wiki/`.
- The ingest pipeline orchestration (how `parse → chunk → extract → resolve` is wired together) is a thin layer — the current PRD focuses on the extraction + resolution modules themselves.
- Auth/RBAC (flagged in ADR-0004) is out of scope.
- MinerU parsing path (ADR-0002) is out of scope — extraction works on `ParsedChunk` regardless of parser.
- Image/table entity extraction from non-text chunks (e.g., extracting people from an image caption) is handled by the same extraction pipeline since image captions are already captioned-to-text (ADR-0003).

## Further Notes

- The `Entity` model already has `status: published | pending_review` (ADR-0010). In v1, extraction always creates entities with `status=published`.
- The `Relation` model already has `chunk_id` (FK to `chunks`). Relations are extracted with their provenance baked in.
- The `Entity` embedding column already exists. No migration is required to add the column, but the `entity_merge_log` table requires an Alembic migration.
- `extraction.py` and `resolution.py` should be imported from the `rag_wiki.graph` package. The `rag_wiki.ingest` module will call these functions from the pipeline orchestration.
- The `merge.py` helper should be extracted as a deep module if the merge logic is more than a few lines. If it stays small (e.g. < 50 lines), it can be inlined into `resolution.py` and extracted later.
