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
