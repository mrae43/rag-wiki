# ADR-0006: Store wiki pages in Postgres, with optional file export

## Status
Accepted

## Context
The "wiki" layer (LLM-curated, human-facing markdown pages derived from the
knowledge graph — see CONTEXT.md) needs a storage location. The original LLM Wiki
pattern stores pages as files in a git repo, browsed in Obsidian. This project's
deployment model (ADR-0004: single-tenant, but potentially multi-instance for an
enterprise service) and job processing model (ADR-0005: Postgres-native queue)
both favor keeping state in Postgres.

Options considered:

1. **Postgres table** (`wiki_pages`: entity_id FK, title, markdown content,
   version, generated_at), with an optional async export job to write pages to
   files (e.g. for Obsidian).
2. **Git repo of markdown files**, with Postgres only indexing/pointing to them.
3. **Both**, kept in sync.

## Decision
Store wiki pages in a **Postgres table** (option 1). File export (e.g. to a
git-backed directory for Obsidian) is an optional, on-demand/async feature, not
the source of storage.

## Rationale
- **Scalability and statelessness**: API/worker instances remain stateless;
  horizontal scaling requires no shared filesystem (NFS/EFS) or git coordination.
  Git-based storage (option 2) becomes a write bottleneck under concurrent,
  automated regeneration and requires shared storage across instances.
- **Consistency with the rest of the architecture**: continues the "single
  Postgres backend" narrative (vectors, graph, jobs, now wiki content) from
  ADR-0001/0003/0005.
- **API/library-first access**: enterprise consumers (and the FastAPI layer) can
  read/write wiki content directly via Postgres without filesystem access —
  important if the wiki is to be embedded in other systems, not just browsed.
- **Avoids drift** (option 3's risk): there is exactly one place wiki content
  lives; file export is a derived, regenerable artifact.

## Consequences
- The original LLM Wiki pattern's Obsidian/git workflow (graph view, Dataview,
  version history) becomes an **export feature**: a job that renders
  `wiki_pages` rows to a directory of `.md` files (optionally git-committed).
  This is useful for users who want that workflow, but is not load-bearing.
- `wiki_pages` needs its own versioning/history strategy if "what changed and
  when" matters (could reuse `log.md`-style append-only history, but as a table
  — e.g. `wiki_page_revisions`).
- Large wiki pages are fine in Postgres (TOAST handles large text columns), so no
  practical size constraint from this choice.
