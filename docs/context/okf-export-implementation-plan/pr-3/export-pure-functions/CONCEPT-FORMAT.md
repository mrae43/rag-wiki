# Concept Notes — PR-3 `export.py` pure functions

> Generated after Phase 2 (Socratic Questioning), grounded in the approach the user committed to. This is the mental model, not the implementation.

## Core concept(s)
- **Canonical slug map:** A single, precomputed lookup from slug to display name that covers entity pages, source-summary pages, and forward links to not-yet-written pages.
- **Render-time transformation:** Converting internal `[[slug]]` Obsidian links into OKF markdown links without modifying the source-of-truth `wiki_pages.content`.
- **Tolerant link rewriting:** Emitting markdown links for missing slugs rather than failing, matching OKF's forward-link tolerance.

## Why it matters here
The exporter is a pure renderer: Postgres stays the source of truth, and the bundle is derived. The slug map is the lens that turns internal slugs into human-readable labels and correct relative paths. If the map is incomplete or the rewrite is wrong, the exported bundle is silently broken for Obsidian/OKF consumers without ever touching the DB.

## Mental model / analogy
Like translating a screenplay's internal scene numbers into public chapter headings for a published book. The original script stays untouched; the publisher's map decides what the reader sees. A missing scene number becomes a placeholder chapter title rather than a missing page.

## Common pitfalls
- Building the map from only entity pages and forgetting source-summary pages, so links to source summaries break or get wrong labels.
- Using a regex that also rewrites `[[...]]` inside code blocks or image syntax.
- Reimplementing slug generation instead of calling the existing `generate_slug()` function, causing silent mismatches when entity names change.

## Related patterns in this domain
- `rag_wiki/wiki/synthesis.py` already builds the same slug→entity/source relationship when creating pages; the exporter reads that result rather than writes it.
- `rag_wiki/retrieval/` uses `wiki_pages.content` directly and never sees exported links; the export transformation must not leak back into retrieval.

## Optional further reading
- ADR-0019 §2 (front-matter field set) and §5 (inline link rewrite rules)
