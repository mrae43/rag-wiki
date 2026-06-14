# ADR-0009: Hybrid retrieval (vector seed + graph traversal), single mode for v1

## Status
Accepted

## Context
Query-time retrieval needs to combine the vector embeddings (ADR-0003) and
knowledge graph (ADR-0001) to produce context for answering questions. RAG-Anything
/ LightRAG offer multiple retrieval modes (e.g. "local", "global", "hybrid",
"naive") tuned for different query types.

Options considered:

1. **Vector-only**: top-k similarity search over `chunks.embedding`, return those
   chunks as context. Ignores the graph entirely.
2. **Hybrid, single mode**: vector search over both chunk and entity embeddings
   to find seed nodes, then graph traversal (recursive CTE over `entities`/
   `relations`, ADR-0001) outward from those seeds to pull in connected context,
   combined with the seed chunks into the final context set.
3. **Multiple selectable modes**, mirroring LightRAG's local/global/hybrid/naive
   distinction — closer to the reference architecture, but a much larger surface
   area to design, implement, and test.

## Decision
Implement **hybrid, single-mode retrieval** (option 2) for v1: vector search to
find seed entities/chunks, recursive CTE traversal to expand graph context around
those seeds, combined results passed to the LLM for answer synthesis.

The retrieval function's internals (seed-finding, traversal, context assembly)
should be separable steps, so additional modes could be added later without
rewriting the pipeline — but only one mode ships initially.

## Rationale
- **Uses the graph, unlike option 1** — the knowledge graph (ADR-0001) and entity
  resolution (ADR-0008) only provide value if retrieval actually traverses it;
  vector-only retrieval would make most of the architecture's effort pointless.
- **Bounded scope vs option 3** — replicating LightRAG's full mode set is a large
  surface area whose main value is benchmarking against academic baselines. For a
  portfolio/enterprise product, one well-designed retrieval path that
  demonstrably uses both vectors and graph is more convincing than several
  partially-tuned modes.
- **Composable internals**: designing seed-finding, traversal, and context
  assembly as separate, swappable steps means a "global" (graph-first) or
  "naive" (vector-only) mode could be added later as alternative
  configurations of the same pipeline, not a separate system.

## Consequences
- Need to define traversal depth/breadth limits (how many hops, how many nodes
  per hop) to bound context size and query latency — likely configurable.
- Context assembly needs a strategy for combining chunk content and
  entity/relation summaries into a single prompt context, with some ranking/
  truncation when the combined context exceeds the LLM's context window.
- "Local" vs "global" query distinctions (LightRAG's terms for entity-focused vs
  theme-focused questions) are not exposed to users in v1 — the single hybrid
  mode is expected to handle both reasonably, with mode-selection deferred as
  future work if evaluation shows it's needed.
