# ADR-0008: Real-time entity resolution via embeddings + LLM, with periodic lint as backup

## Status
Accepted

## Context
During ingestion, the extraction step (ADR-0007's LLM calls) identifies entities
and relations from each chunk. The same real-world entity ("Apple Inc.",
"Apple", "AAPL") may be extracted with different surface forms across chunks and
sources. Without resolution, the knowledge graph (ADR-0001) accumulates duplicate
nodes, fragmenting the wiki (ADR-0006) instead of converging it.

Options considered:

1. **Exact string match only** (case-insensitive) — cheap, but misses any naming
   variation; duplicates accumulate immediately.
2. **Real-time resolution via embeddings + LLM**: for each newly extracted
   entity, search existing entities by name/description embedding similarity
   (pgvector, reusing ADR-0003's infrastructure), and have the LLM decide
   merge-vs-new for close candidates, at ingest time.
3. **Defer to periodic "lint"**: allow duplicates at ingest; a separate batch
   process (matching the source pattern's "Lint" operation) periodically finds
   and merges duplicates across the graph.

## Decision
Use **real-time resolution (option 2) as the primary mechanism**, with a
**periodic lint pass (option 3) as a backup/safety net** for anything real-time
resolution misses (e.g. entities that only become recognizably duplicate once a
third, clarifying source is ingested).

## Rationale
- **Matches the core value proposition of the LLM Wiki pattern**: cross-references
  and synthesis should be "already there" after each ingest, not re-derived or
  cleaned up later. A wiki that's fragmented between ingests and only
  periodically repaired feels less "alive" than one that's coherent immediately.
- **Reuses existing infrastructure**: entity name/description embeddings can use
  the same pgvector setup as chunk embeddings (ADR-0003) — no new embedding
  pipeline needed, just another embedded column/table.
- **"Organize for actionability, not perfection"** (CODE/PARA framing): real-time
  resolution doesn't need to be flawless — it needs to be good enough that the
  graph stays usable between ingests. The lint pass exists precisely to catch the
  cases real-time resolution gets wrong, without making real-time resolution
  itself need to be perfect.
- **Lint pass earns a clear, separate role**: rather than being the primary dedup
  mechanism (option 3 alone) or redundant (if option 2 were perfect), lint
  becomes the place where *cross-source contradictions* and *judgment calls
  needing more context* are surfaced — consistent with the source pattern's
  description of lint (contradictions, stale claims, orphan pages).

## Consequences
- Ingestion's extraction step grows a resolution sub-step: embed candidate
  entity → vector search existing entities → LLM merge decision for top
  candidates → either merge (update existing entity, record new source as
  evidence) or create new entity.
- This adds LLM calls and vector searches to the ingest critical path — needs to
  be accounted for in job processing time estimates (ADR-0005).
- The lint pass needs to be a defined, schedulable operation (not just an ad-hoc
  prompt) — likely its own job type in the Postgres queue (ADR-0005), producing a
  report of suggested merges/contradictions for review (human-in-the-loop,
  matching the source pattern's preference for staying involved).
- Entity merge needs an audit trail (which entities were merged, when, why) —
  relevant both for debugging and for the "audit logging" requirement from
  ADR-0004.
