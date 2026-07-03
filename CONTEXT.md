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
be regenerated or repaired from the database. It is the human-facing layer (e.g.
browsed in Obsidian), analogous to a compiled artifact built from source.

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
