# Agent Harness for rag-wiki

> A conceptual mapping of the [16-step agent harness blueprint](harness-engineering.md) onto the rag-wiki ingestion and query pipelines. This document is not a new framework — it formalizes design principles that are already present in the system and identifies where they can be made explicit.

---

## 1. Why a Harness?

The rag-wiki system already contains all the primitives of a reliable agent harness: a [job queue](adr/0005-postgres-job-queue.md), a [provider abstraction](adr/0007-llm-provider-abstraction.md) for tools, [Postgres-native memory](adr/0001-relational-knowledge-graph.md) for the knowledge graph, and [structured retrieval](adr/0009-hybrid-retrieval.md) for context control. What is missing is a **unified conceptual layer** that treats these components as a single agent system with a clear job, workflow, and self-evaluation.

This document maps the harness blueprint to the two primary agent surfaces in the project:

1. **The Ingestion Harness** — the pipeline that converts a raw [Source](../CONTEXT.md) into [Chunks](../CONTEXT.md), [Entities](../CONTEXT.md), [Relations](../CONTEXT.md), and [Wiki](../CONTEXT.md) pages.
2. **The Query/Retrieval Harness** — the pipeline that answers a user question using the knowledge graph.

---

## 2. Design Principles (Project Mapping)

| Harness Principle | How rag-wiki Already Embodies It | Relevant ADR / Doc |
|---|---|---|
| **Clear job** | Every pipeline stage has one measurable outcome (parse → extract → resolve → embed → synthesize). | [ADR-0010](adr/0010-automated-ingestion.md) |
| **Controlled tools** | All LLM calls go through the `LLMProvider` protocol; parser selection is feature-flagged. | [ADR-0007](adr/0007-llm-provider-abstraction.md), [ADR-0002](adr/0002-hybrid-parsing-pipeline.md) |
| **Focused context** | Hybrid retrieval uses vector search for seeding and recursive CTEs for traversal; no stuffing. | [ADR-0009](adr/0009-hybrid-retrieval.md), [ADR-0003](adr/0003-caption-to-text-embeddings.md) |
| **Strong checks** | Entity resolution lint pass is a post-hoc safety net; wiki synthesis has a self-evaluation opportunity. | [ADR-0008](adr/0008-entity-resolution.md) |
| **Slow improvement** | The ADR process itself is the incremental improvement mechanism. | [docs/adr/](.) |

---

## 3. The Ingestion Harness

### 3.1 Job Definition
- **Primary task:** Convert a raw `Source` into durable knowledge: `Chunk`s, `Entity`s, `Relation`s, and `Wiki` pages.
- **Success criteria:** The source is fully parsed, all entities are resolved against the existing graph, and the wiki pages are updated to reflect the new knowledge.

### 3.2 Model Selection
- **Per-operation selection** is already implemented via environment variables:
  - `LLM_MODEL_CAPTION` — for image/table captioning
  - `LLM_MODEL_EXTRACTION` — for entity/relation extraction
  - `LLM_MODEL_WIKI_SYNTHESIS` — for wiki page generation
  - `EMBEDDING_MODEL` — for vector generation
- This aligns with the harness principle of matching model tier to task complexity.
- See [ADR-0007](adr/0007-llm-provider-abstraction.md) and [coding-standards.md §9](coding-standards.md).

### 3.3 System Instructions
- The agent's role is defined by prompt templates (not yet consolidated into a single "rulebook").
- **Role:** "You are a knowledge extraction assistant. Extract entities and relations from the provided text."
- **Boundaries:** Do not hallucinate facts not present in the source. Do not skip entities.
- **Stop conditions:** If the source is empty or unparseable, return an empty list and report the failure.
- **Help-seeking:** If entity resolution confidence is below the threshold, flag for human review.

### 3.4 Workflow Design

```
Receive Source
  → Parse (lightweight or MinerU)
  → Caption (images, tables, equations)
  → Extract (entities and relations from chunks)
  → Resolve (merge or create new entities)
  → Embed (chunk and entity vectors)
  → Synthesize (update wiki pages)
  → Evaluate (lint pass + self-check)
  → Return (commit to Postgres)
```

- Entry point: `rag_wiki.ingest` pipeline triggered by a job from the [Postgres job queue](adr/0005-postgres-job-queue.md).
- Exit condition: All chunks processed, entities resolved, wiki pages updated, and job status set to `completed`.

### 3.5 Tools

| Tool | Workflow Step | Implementation |
|---|---|---|
| Lightweight parser | Parse | `pymupdf` + `unstructured` ([ADR-0002](adr/0002-hybrid-parsing-pipeline.md)) |
| MinerU parser | Parse (optional) | Feature-flagged, fallback on failure ([ADR-0002](adr/0002-hybrid-parsing-pipeline.md)) |
| `LLMProvider.caption_image()` | Caption | Per `LLM_MODEL_CAPTION` ([ADR-0007](adr/0007-llm-provider-abstraction.md)) |
| `LLMProvider.complete()` | Extract, Synthesize | Per `LLM_MODEL_EXTRACTION` / `LLM_MODEL_WIKI_SYNTHESIS` |
| `LLMProvider.embed()` | Embed | Per `EMBEDDING_MODEL` ([ADR-0003](adr/0003-caption-to-text-embeddings.md)) |
| Entity Resolver | Resolve | Embedding similarity + LLM merge decision ([ADR-0008](adr/0008-entity-resolution.md)) |
| Postgres DB | Memory / Commit | All durable state ([ADR-0001](adr/0001-relational-knowledge-graph.md), [ADR-0006](adr/0006-wiki-pages-in-postgres.md)) |

### 3.6 Memory

| Memory Type | Implementation | Data Stored |
|---|---|---|
| **Short-term** | Job payload + in-memory state | Current chunk, extraction result, candidate entity |
| **Long-term** | `entities`, `relations` tables | Stable graph structure, entity properties |
| **Retrieval** | `chunks` + `wiki_pages` + pgvector | Source content, synthesized knowledge, embeddings |

- The Postgres database is the single source of truth for all memory types.
- See [ADR-0001](adr/0001-relational-knowledge-graph.md), [ADR-0006](adr/0006-wiki-pages-in-postgres.md).

### 3.7 Context Control
- **During extraction:** Only the current chunk text (plus its caption if it is an image) is passed to the LLM. No full document stuffing.
- **During resolution:** The candidate entity's embedding is used to find the top-k most similar existing entities via pgvector. Only those candidates are passed to the LLM for the merge decision.
- **During wiki synthesis:** Relevant chunks and entity summaries are retrieved via hybrid search and assembled into a context window, ranked by relevance.

### 3.8 Planning
- **Current state:** The pipeline is sequential, not planned. The workflow is implicit in the code.
- **Harness opportunity:** A lightweight planning step could classify the source type (e.g., academic paper, invoice, image-heavy) and select the appropriate parser and model parameters before execution.

### 3.9 Tool Rules

| Tool | Trigger | Input Schema | Failure Handling |
|---|---|---|---|
| MinerU parser | Source type = PDF + `MINERU_ENABLED=true` | PDF bytes | Fallback to lightweight parser; log warning ([coding-standards.md §2.4](coding-standards.md)) |
| LLM caption | Chunk type = image/table/equation | Image bytes + prompt | Skip caption, log warning; chunk may be excluded from embedding |
| LLM extraction | Chunk type = text | Chunk text + system prompt | Retry 3x with backoff; if still failing, mark chunk as failed and continue |
| Entity resolver | After extraction | Candidate entity + top-k matches | If confidence < threshold, create new entity (never force-merge) |

### 3.10 Evaluation
- **Current state:** The entity resolution lint pass ([ADR-0008](adr/0008-entity-resolution.md)) is a post-hoc safety net.
- **Harness opportunity:** A self-evaluation step before writing to the database could check:
  - **Completeness:** Were all chunks processed? Are there orphaned chunks?
  - **Consistency:** Do new entities contradict existing relations?
  - **Format:** Are wiki pages valid markdown? Do they cite their sources?

### 3.11 Error Handling
- All failures are handled per [coding-standards.md §2](coding-standards.md):
  - Specific exception types (`IngestError`, `LLMProviderError`, `EntityResolutionError`)
  - Retry with exponential backoff at the LLM provider boundary
  - Graceful fallback for optional paths (MinerU → lightweight)
  - Full traceback logged at the job runner boundary

### 3.12 Human Approval
- **Current state:** The `status` column on `entities`, `relations`, and `wiki_pages` defaults to `published` ([ADR-0010](adr/0010-automated-ingestion.md)). This is the structural hook for a future approval workflow.
- **Harness opportunity:** Define which actions require human approval (e.g., merging two high-confidence entities, deleting a wiki page, publishing a new entity in a sensitive domain). The harness should gate these behind the `pending_review` status.

### 3.13 Logging
- All major events are logged via `structlog` with domain IDs ([coding-standards.md §6](coding-standards.md)):
  - `job started/completed/failed`
  - `entity resolved/created/merged`
  - `wiki page updated`
  - `provider call failed` (with attempt count)
- The audit trail for entity merges is a future enhancement flagged in [ADR-0010](adr/0010-automated-ingestion.md).

### 3.14 Testing
- The test structure mirrors the source structure ([coding-standards.md §8](coding-standards.md)).
- **Harness-specific test recommendations:**
  - Malformed PDFs (missing fonts, corrupted streams)
  - Empty or image-only sources
  - LLM timeout mid-extraction
  - Duplicate sources that trigger resolution edge cases

### 3.15 Improvement
- Changes are made incrementally via the ADR process.
- The harness should be versioned alongside the ADRs that affect it.
- Track metrics: ingestion throughput, entity merge accuracy, wiki page freshness.

---

## 4. The Query/Retrieval Harness

### 4.1 Job Definition
- **Primary task:** Answer a user question using the knowledge graph.
- **Success criteria:** The answer is accurate, cites its sources, and acknowledges when the graph does not contain the requested information.

### 4.2 Model Selection
- `LLM_MODEL_QUERY` — configured via environment variable, consistent with [ADR-0007](adr/0007-llm-provider-abstraction.md).
- This is typically the strongest/cheapest model available, as query volume is high and the task is primarily synthesis, not deep reasoning.

### 4.3 System Instructions
- **Role:** "You are a knowledge assistant. Answer the question using only the provided context."
- **Boundaries:** Do not use external knowledge. Do not hallucinate citations.
- **Stop conditions:** If the context is empty or irrelevant, respond with "I don't have enough information to answer that."
- **Help-seeking:** If the question is ambiguous, ask for clarification.

### 4.4 Workflow Design

```
Receive Question
  → Plan (classify query, determine retrieval depth)
  → Retrieve (vector seed + graph traversal)
  → Assemble Context (ranked chunks + entity summaries)
  → Synthesize (generate answer with citations)
  → Evaluate (self-check against retrieved context)
  → Return (answer + source IDs)
```

### 4.5 Tools

| Tool | Workflow Step | Implementation |
|---|---|---|
| Vector search (pgvector) | Retrieve | Seed-finding via chunk embedding similarity ([ADR-0009](adr/0009-hybrid-retrieval.md)) |
| Graph traversal (recursive CTE) | Retrieve | Expand from seed entities to related entities/relations ([ADR-0001](adr/0001-relational-knowledge-graph.md)) |
| `LLMProvider.complete()` | Synthesize | Per `LLM_MODEL_QUERY` ([ADR-0007](adr/0007-llm-provider-abstraction.md)) |

### 4.6 Memory
- Same Postgres backend as the Ingestion Harness:
  - `entities`, `relations` for the graph structure
  - `chunks` for raw source content
  - `wiki_pages` for synthesized, human-readable summaries

### 4.7 Context Control
- **Seed-finding:** Vector search returns top-k chunks. These are ranked by cosine similarity.
- **Graph traversal:** From the seed chunks, related entities and their neighbors are traversed up to a configurable depth.
- **Pruning:** Weak matches (similarity below threshold) are discarded. The assembled context is truncated to the LLM's context window.
- **Ranking:** Wiki pages are prioritized if the query is broad; specific chunks are prioritized if the query is narrow.

### 4.8 Planning
- **Current state:** The query is passed directly to retrieval without a planning step.
- **Harness opportunity:** A lightweight planner could classify the query:
  - **Factual lookup:** "What is X?" → shallow retrieval, prioritize wiki pages.
  - **Relationship query:** "How does X relate to Y?" → deeper graph traversal.
  - **Summarization:** "Summarize the document about Z." → broad retrieval, aggregate chunks.

### 4.9 Tool Rules

| Tool | Trigger | Input Schema | Failure Handling |
|---|---|---|---|
| Vector search | Always (first step) | Query embedding | If no results, return "No relevant documents found" |
| Graph traversal | Seed entities found | Seed entity IDs + max_depth | If traversal returns empty, answer from seeds only |
| LLM synthesis | Context assembled | Context + question + system prompt | Retry 3x; if failing, return error to user |

### 4.10 Evaluation
- **Self-check before returning:**
  - Does the answer contain claims not supported by the context?
  - Are all citations valid (do they point to existing chunks/entities)?
  - Is the answer concise and relevant?
- **Harness opportunity:** This evaluation step could be a separate LLM call or a rule-based checker.

### 4.11 Error Handling
- **Empty retrieval:** If no chunks or entities are found, the harness returns a graceful "I don't know" instead of hallucinating.
- **LLM failure:** If the synthesis call fails after retries, return a service error and log the failure.
- **Timeout:** If retrieval is too slow, return a partial answer with a note that the full graph was not searched.

### 4.12 Human Approval
- **Not applicable for pure queries** (no writes to the database).
- **Future applicability:** If the query pipeline is extended to allow users to edit the wiki (e.g., "Add a note about X"), that write should be gated by approval.

### 4.13 Logging
- Log the query, the plan, the tools used (vector search, graph traversal), the retrieved sources, and the final answer.
- This enables debugging of retrieval quality and hallucination incidents.

### 4.14 Testing
- **Harness-specific test recommendations:**
  - Ambiguous questions ("What about X?" with no context)
  - Questions about entities not in the graph
  - Questions that require multi-hop reasoning (A → B → C)
  - Very long queries that test context window limits
  - Adversarial queries that try to elicit off-topic responses

### 4.15 Improvement
- Track query accuracy, retrieval precision/recall, and user satisfaction.
- Tune retrieval parameters (top-k, max traversal depth) incrementally.
- Update system instructions based on common failure modes.

---

## 5. Master Evaluation Scorecard

Use this scorecard to assess the maturity of the Ingestion and Query harnesses at each release.

### Ingestion Harness Scorecard

| # | Component | Key Question | Pass? |
|---|---|---|---|
| 1 | Job Definition | Is there one clear, testable task? | ✅ |
| 2 | Model Selection | Is the model tier appropriate per operation? | ✅ ([ADR-0007](adr/0007-llm-provider-abstraction.md)) |
| 3 | System Instructions | Are role, scope, stop conditions, and escalation defined? | ⚠️ (Prompts exist, but not consolidated into a rulebook) |
| 4 | Workflow Design | Is the flow staged, documented, and repeatable? | ✅ ([ADR-0010](adr/0010-automated-ingestion.md)) |
| 5 | Tools | Are tools minimal, justified, and scoped? | ✅ ([ADR-0002](adr/0002-hybrid-parsing-pipeline.md), [ADR-0007](adr/0007-llm-provider-abstraction.md)) |
| 6 | Memory | Are the right memory types used for each data category? | ✅ ([ADR-0001](adr/0001-relational-knowledge-graph.md), [ADR-0006](adr/0006-wiki-pages-in-postgres.md)) |
| 7 | Context Control | Is context filtered, ranked, and pruned? | ✅ ([ADR-0009](adr/0009-hybrid-retrieval.md)) |
| 8 | Planning | Does the agent plan before acting? | ⚠️ (Sequential pipeline, no explicit planning step) |
| 9 | Tool Rules | Does each tool have triggers, schemas, and failure handling? | ✅ ([ADR-0002](adr/0002-hybrid-parsing-pipeline.md), [coding-standards.md §2.4](coding-standards.md)) |
| 10 | Evaluation | Does the agent check its own output before returning? | ⚠️ (Lint pass is post-hoc; no pre-commit evaluation gate) |
| 11 | Error Handling | Are failures handled with recovery, not silent stops? | ✅ ([coding-standards.md §2](coding-standards.md), [ADR-0005](adr/0005-postgres-job-queue.md)) |
| 12 | Human Approval | Are risky/irreversible actions gated by a human? | ⚠️ (`status` column exists; no approval workflow yet) |
| 13 | Logging | Are decisions, tools, errors, and outputs logged? | ✅ ([coding-standards.md §6](coding-standards.md)) |
| 14 | Real-world Testing | Has the system been tested on messy, imperfect inputs? | ⚠️ (Standard tests exist; harness-specific "messy input" suite recommended) |
| 15 | Iteration Process | Are improvements made incrementally with tracking? | ✅ (ADR process) |

### Query/Retrieval Harness Scorecard

| # | Component | Key Question | Pass? |
|---|---|---|---|
| 1 | Job Definition | Is there one clear, testable task? | ✅ |
| 2 | Model Selection | Is the model tier appropriate per operation? | ✅ ([ADR-0007](adr/0007-llm-provider-abstraction.md)) |
| 3 | System Instructions | Are role, scope, stop conditions, and escalation defined? | ⚠️ (Prompts exist, but not consolidated into a rulebook) |
| 4 | Workflow Design | Is the flow staged, documented, and repeatable? | ✅ ([ADR-0009](adr/0009-hybrid-retrieval.md)) |
| 5 | Tools | Are tools minimal, justified, and scoped? | ✅ ([ADR-0009](adr/0009-hybrid-retrieval.md)) |
| 6 | Memory | Are the right memory types used for each data category? | ✅ ([ADR-0001](adr/0001-relational-knowledge-graph.md), [ADR-0006](adr/0006-wiki-pages-in-postgres.md)) |
| 7 | Context Control | Is context filtered, ranked, and pruned? | ✅ ([ADR-0009](adr/0009-hybrid-retrieval.md)) |
| 8 | Planning | Does the agent plan before acting? | ⚠️ (No query classification or planning step) |
| 9 | Tool Rules | Does each tool have triggers, schemas, and failure handling? | ✅ (Retrieval pipeline is well-defined) |
| 10 | Evaluation | Does the agent check its own output before returning? | ⚠️ (No self-check for hallucinations) |
| 11 | Error Handling | Are failures handled with recovery, not silent stops? | ✅ (Graceful "I don't know" for empty retrieval) |
| 12 | Human Approval | Are risky/irreversible actions gated by a human? | N/A (Query is read-only) |
| 13 | Logging | Are decisions, tools, errors, and outputs logged? | ✅ ([coding-standards.md §6](coding-standards.md)) |
| 14 | Real-world Testing | Has the system been tested on messy, imperfect inputs? | ⚠️ (Harness-specific adversarial query suite recommended) |
| 15 | Iteration Process | Are improvements made incrementally with tracking? | ✅ (ADR process) |

---

## 6. Gaps & Opportunities

The following are not failures of the current system — they are natural next steps for making the harness explicit.

### 6.1 Explicit Planning (Step 8)
- **Ingestion:** A lightweight pre-analysis of the source type could select the optimal parser and model parameters before the pipeline begins.
- **Query:** A query classification step could determine retrieval depth and whether to prioritize wiki pages or raw chunks.

### 6.2 Formal Evaluation Gates (Step 10)
- **Ingestion:** A self-check before writing to the database could verify completeness, consistency, and format validity.
- **Query:** A post-synthesis check could verify that the answer does not contain hallucinations and that all citations are valid.

### 6.3 Human Approval Workflow (Step 12)
- The `status` column (`published` / `pending_review`) is the structural foundation for this feature.
- The harness should define the trigger conditions for moving an entity, relation, or wiki page into `pending_review`.

### 6.4 Harness-Specific Test Suites (Step 14)
- **Ingestion:** Tests for malformed PDFs, empty sources, LLM timeout mid-pipeline, and duplicate-source resolution.
- **Query:** Tests for ambiguous questions, out-of-scope queries, multi-hop reasoning, and adversarial inputs.

---

## 7. Conclusion

The rag-wiki system is already a well-designed agent harness in practice. The Postgres backend is the memory, the job queue is the workflow engine, the `LLMProvider` protocol is the tool interface, and the hybrid retrieval pipeline is the context controller. This document maps those existing components to the harness vocabulary so that future improvements — planning, evaluation, approval — can be added incrementally and with clear intent.

The harness is not a separate system to build. It is a **lens for evaluating and improving the system we already have**.

---

*See also: [docs/harness-engineering.md](harness-engineering.md) for the full 16-step blueprint.*
