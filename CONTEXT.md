# Context

A glossary of terms and concepts for this project. This file is descriptive only —
no implementation details. See `docs/adr/` for decisions and their rationale.

## Terms

### Source of Truth
The **Postgres database** (raw source metadata, chunks, embeddings, knowledge graph
entities/relations). All durable knowledge lives here.

### Wiki
A set of **LLM-maintained markdown pages** that present a curated, synthesized view
*derived from* the Postgres knowledge graph. The wiki is not authoritative — it can
be regenerated or repaired from the database. It lives in Postgres and is the
source of truth for synthesized knowledge. Its file-based rendering is the
**Knowledge Bundle**.

### Knowledge Bundle
The exported, file-based rendering of the **Wiki** as a directory of markdown
concept files, each with structured front-matter and markdown links to other
concepts in the bundle. Derived and regenerable from the Postgres Wiki; never the
source of truth. Consumed by humans (e.g. browsed in Obsidian) and by AI agents.
One Wiki page maps to one concept file in the bundle.

### Source
A raw input document (PDF, article, image, etc.) ingested into the system. Immutable
once stored; the system reads from it but never modifies it.

### Chunk
A unit of extracted content from a Source (text block, table, image, equation, etc.),
the atomic unit that gets embedded and linked into the knowledge graph.

### Entity / Relation
Nodes and edges of the knowledge graph, extracted from Chunks. Entities represent
real-world concepts (people, places, ideas); Relations represent how they connect.

### Seed
An **entity** used as the starting point for a graph traversal during retrieval.
Seeds are found by vector-similarity search of the user query against entity
embeddings, or provided directly for entity navigation.

### Planner
Classifies documents (by density and content type) and queries (by intent and
complexity), then routes each operation to the optimal processing strategy and
model. Planner decisions are logged for provenance.

### Provider
An abstraction over LLM backends. **ChatProvider** handles text completions and
image captioning; **EmbeddingProvider** handles text embeddings. Never call LLM
SDKs directly outside `rag_wiki/providers/`.

### Job / Queue
A Postgres-native job queue (`jobs` table) using `SELECT FOR UPDATE SKIP LOCKED`
for claiming. Interface: `enqueue()`, `claim_next()`, `complete_job()`,
`fail_job()`, `release_claim_to_pending()`. The worker runs as a separate
process (`rag_wiki.worker`).

### Storage
An abstraction over file storage. **LocalStorageProvider** (filesystem, default)
and **S3StorageProvider** (S3-compatible backends like SeaweedFS / MinIO). Swap
by configuration, no application code changes.

### MCP Server
A FastMCP wrapper that exposes RagWiki knowledge graph tools (query, retrieve)
via stdio or Streamable HTTP for MCP hosts (Obsidian, Claude Desktop, VS Code).
Proxies requests to the backend FastAPI.

### Graph Analysis Run
A single batch execution of community detection, PageRank, and surprising-connection
detection over the published knowledge graph. Produces an immutable persisted
snapshot of Communities, memberships, cohesions, centralities, and Surprising
Connections for that run. A later run may produce different Communities; old runs
are retained for diffing.
_Avoid_: analysis pass, graph rebuild, cluster run

### Community
A set of Entities grouped together by a Graph Analysis Run because they share many
Relations among themselves and comparatively fewer with Entities outside the group.
Specific to one Graph Analysis Run; not persisted across runs as the same Community.
_Avoid_: cluster, module, theme

### Cohesion
A 0–1 density score per Community: how internally connected the Community is relative
to its maximum possible internal Relations. An input to Surprising Connection, not a
quality judgement about the Community.
_Avoid_: density, score, quality

### God Node
An Entity that ranks highest within its run by PageRank over the directed, weighted
knowledge graph — a "most-referred-to" concept, not necessarily the most semantically
important one. Recovered from the persisted per-run PageRank scores.
_Avoid_: hub, central node, important entity

### Surprising Connection
An inter-Community Relation whose endpoints both carry high PageRank and whose
Communities both have low Cohesion — flagged as a notable bridge between otherwise
disconnected areas. Survives a top-K ranking threshold per run.
_Avoid_: bridge edge, anomaly, weird link

### Graph View
A transient, in-memory graph constructed from published Entities and Relations for
the duration of one Graph Analysis Run. Loaded from Postgres at run start, discarded
at run end. Never a backend, never persisted — distinct from the knowledge graph
itself, which lives in Postgres.
_Avoid_: analysis graph, the graph, internal graph

## Roles

### Backend (rag_wiki)
The headless **AI system**: FastAPI + worker + MCP + Postgres. Owns the
knowledge graph, retrieval, and synthesis. No user-facing UI; accessed via its
API and MCP transport.
_Avoid_: AI systems, this project, the backend service

### Interface App
A separate full-stack application that renders the wiki for end users and owns
authentication. Calls the Backend's API server-side.
_Avoid_: dedicated app, dedicated full-stack application, the interfaces

### Client
Any system that calls the Backend's API or MCP transport: an Interface App, an
Obsidian plugin, an automation script, or a Copilot Chat session.
_Avoid_: consumer, integrator

## Relationships

- A **Client** calls the **Backend (rag_wiki)** via its API or MCP transport.
- An **Interface App** is a **Client** that additionally owns end-user
  authentication; it proxies or gates access to the **Backend**.
- The **Backend** does not authenticate **Clients** in Stage-1; isolation is
  enforced by the network (trusted-clients-only).
- A **Graph Analysis Run** reads the published knowledge graph into a transient
  **Graph View**, computes **Communities** with per-community **Cohesion**,
  **God Nodes** via PageRank, and **Surprising Connections** ranked by an edge
  bridge score, then persists all four as immutable rows keyed to the run.
- A **Community** belongs to exactly one **Graph Analysis Run**; an **Entity**
  belongs to at most one **Community** within a run (singletons allowed for
  isolated Entities). The latest completed run is the current view of how the
  graph clustered; older runs are retained for comparison.

## Flagged ambiguities

- "graph" was used to mean both the **knowledge graph** (the persisted
  `entities`/`relations` in Postgres, the Source of Truth) and a **Graph View**
  (transient in-memory analysis artifact). Resolved: these are distinct. The
  knowledge graph is durable storage; a Graph View is recomputed each run.
- "cluster" was used to mean both the action of running community detection and
  its result. Resolved: the action is **Cluster** (a step inside a Graph Analysis
  Run); the result is a **Community**.
- "hub"/"central node" was used loosely for high-pagerank Entities. Resolved:
  the canonical term is **God Node**, scoped to a single run's PageRank ranking.
