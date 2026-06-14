# ADR-0001: Store the knowledge graph as plain relational tables in Postgres

## Status
Accepted

## Context
The system needs a knowledge graph (entities and relations extracted from ingested
content, in the spirit of RAG-Anything's dual-graph construction) to support
graph-style traversal during retrieval. Postgres is the project's single backend
(see CONTEXT.md — Source of Truth).

Three options were considered for representing this graph:

1. **Apache AGE** — an openCypher graph extension for Postgres, giving native
   graph query syntax and graph-optimized traversal.
2. **Plain relational tables** — `entities` and `relations` tables, with
   relations as edge rows (source_id, target_id, type, ...), traversed via
   recursive CTEs or application-level BFS/DFS.
3. **JSONB documents** — one JSONB blob per entity with relations embedded as
   arrays/objects.

## Decision
Use **plain relational tables** (option 2): an `entities` table and a `relations`
table (edges as rows), traversed with recursive CTEs and standard indexes.

## Rationale
- **Portability**: runs on any standard Postgres instance (Supabase, Neon, RDS,
  Railway, local Docker) without requiring a non-default extension. Important for
  a portfolio project that needs to be easy to deploy and demo.
- **Transparency**: a reviewer can inspect the schema and run plain SQL to
  understand the graph — no Cypher knowledge required.
- **Sufficient at scale**: a personal/portfolio knowledge wiki will realistically
  hold hundreds to low thousands of entities and relations. Recursive CTEs with a
  few indexes (on entity id, relation source/target/type) comfortably handle
  traversal at this scale.
- **Consistency**: relational tables with foreign keys enforce referential
  integrity between entities and relations, which JSONB blobs would not.

## Consequences
- Multi-hop graph queries are written as recursive CTEs rather than Cypher —
  more verbose, but standard SQL.
- If the graph later grows far beyond portfolio scale (tens of thousands+ of
  entities with dense relations) and traversal performance becomes a bottleneck,
  migrating to Apache AGE or a dedicated graph database is a possible future
  revisit — but is deliberately deferred.
