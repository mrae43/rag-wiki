# ADR-0019: Adopt OKF as the wiki export format

## Status
Accepted

## Context
ADR-0006 established that `wiki_pages` lives in Postgres as the source of
truth and that file export (e.g. for Obsidian) is an optional, derived
artifact. The `rag-wiki export` CLI command has been a stub since that ADR
landed — `rag_wiki/cli.py:95` prints "Export not yet implemented" and exits 1.

In June 2026 Google Cloud published the **Open Knowledge Format (OKF)** — an
open, vendor-neutral spec for representing organizational knowledge as a
directory of Markdown concept files with YAML front-matter and Markdown links
between concepts. OKF formalizes the "LLM-wiki" pattern this project already
follows (ADR-0006 references it directly). OKF is at v0.1 (draft), but its
surface is small enough (front-matter + reserved filenames + link graph) that
adopting it as the export format gives us: producer/consumer independence
(agents and humans read the same artifact), a navigable link graph derived
from our existing `[[slug]]` synthesis output, and two reserved navigation
aids (`index.md`, `log.md`) that map cleanly onto our wiki page model.

A design review resolved 16 sub-decisions across the export pipeline. The
rejected alternatives are recorded per-section so the next engineer doesn't
re-litigate them.

## Decision

### 1. Export format only — Postgres remains the source of truth
OKF is adopted **only** as the format emitted by `rag-wiki export`. The
`wiki_pages` table is unchanged; `content` stays free-form LLM-authored
markdown. The exporter is a pure renderer — it reads Postgres, writes files,
and never feeds back. ADR-0006's "Postgres is SoT, file export is derived"
contract is untouched.

Rejected: making `wiki_pages.content` itself OKF (front-matter + body in the
DB row). That would conflate source-of-truth with a 0.1-draft spec and force
synthesis-prompt + retrieval section-parser changes for no v1 benefit.

### 2. Front-matter field set
Six fields, emitted for every concept file:

| Field | Source | Notes |
|---|---|---|
| `type` | `entity.entity_type` (entity pages) / `"rag-wiki:source-summary"` (source pages) | OKF's only formally-required field |
| `title` | `wiki_pages.title` | |
| `description` | `entity.description` (entity pages, omit if NULL) / first-paragraph excerpt (~200 chars, word-boundary, ellipsis) for source pages | Render-time heuristic; no schema change |
| `timestamp` | `wiki_pages.synthesized_at` | Knowledge valid-time, not export-run time |
| `page_kind` | `"entity"` / `"source_summary"` | Disambiguates the two page kinds orthogonal to `type` |
| `resource` | `{api_base_url}/entities/{entity_id}` (entity) / `{api_base_url}/sources/{source_id}` (source) | OKF-reserved field; reuses `mcp_api_url` as the base — no new setting |

Rejected: emitting internal Postgres UUIDs (`uuid`, `entity_id`, `source_id`,
`slug`) in front-matter. The `resource` URL already carries the round-trip
pointer; leaking PKs throughout the bundle fights OKF's producer/consumer
independence goal and risks colliding with future OKF-reserved field names.

### 3. `type` = entity_type, not page_kind
For entity pages, `type` carries the LLM-extracted domain category
(`person`, `organization`, `concept`, … — free text per
`rag_wiki/prompts/constants.py:13`). The orthogonal page-kind axis lives in
the separate `page_kind` field. This matches OKF's own example
(`type: BigQuery Table` = domain kind) and lets domain consumers filter by
real-world concept type.

### 4. Flat directory layout, two top-level groups
```
<bundle_root>/
├── index.md                          # root listing
├── log.md                            # content changelog
├── .rag-wiki-export-manifest.json    # hidden scratch state (not an OKF concept)
├── entities/
│   ├── index.md
│   ├── {slug}.md
│   └── ...
└── sources/
    ├── index.md
    ├── {slug}.md
    └── ...
```
Concept ID = file path minus `.md` (e.g. `entities/acme-corp-12345678`).
Slugs are `name-<first8 uuid>` (`rag_wiki/wiki/slug.py`), stable across
regenerations as long as `entity.name` doesn't change; OKF's broken-link
tolerance covers renames.

Rejected: sub-grouping by `entity_type` (`entities/person/{slug}.md`).
LLM re-classification on future ingest (ADR-0008/0014) would shift Concept
IDs — consumers dislike moving targets.

### 5. Inline rewrite of `[[slug]]` → OKF Markdown links
The entity synthesis template (`rag_wiki/prompts/templates/synthesize_entity.j2:13,27`)
already instructs the LLM to emit `[[entity-slug]]` Obsidian-style wiki links
for all related entities, forward-referenced or not. The exporter regex-
rewrites every `[[slug]]` inline to `[Label](../entities/slug.md)`, resolving
`Label` from a slug→name map built from `entities` + `wiki_page_entities`.
Links to entities without a wiki page are emitted anyway — OKF spec §9
explicitly tolerates broken/forward links as "not-yet-written knowledge."
Body content stays LLM-faithful; only link syntax changes. Zero prompt
changes.

Rejected: leaving `[[slug]]` untouched (not OKF-conformant; Google's
reference parser won't extract the graph). Rejected: appending a separate
`## OKF Links` data section (duplicates the inline graph; naive consumers
must parse inline links anyway).

### 6. `index.md` at root + per-directory
Root `index.md` lists all concepts grouped by `page_kind`. `entities/index.md`
and `sources/index.md` list their children. Each entry is a Markdown link +
title + one-line `description` (progressive disclosure). Cheap to build from
`wiki_pages` rows; honors OKF's reserved-filename intent.

### 7. `log.md` via hidden manifest diff
A hidden `.rag-wiki-export-manifest.json` at the bundle root stores
`slug → content_hash` for the last successful export. On each run, the
exporter diffs the new hash set against the manifest to compute
added/modified/removed and appends a dated entry to a root `log.md`. The
manifest is scratch state (a dotfile), not an OKF concept — consumers ignore
it.

Rejected: deriving from `wiki_pages.synthesized_at` vs a last-export
timestamp (cannot detect removed pages once the row is gone — a correctness
gap). Rejected: deferring `log.md` to v2 pending a future
`wiki_page_revisions` table (would ship without one of OKF's two reserved
navigation aids).

### 8. Orphan-file deletion
When the manifest-diff detects a removed page (slug in manifest, not in DB),
the exporter deletes the corresponding `.md` file. The bundle always mirrors
Postgres exactly — honors ADR-0006's "file export is derived and
regenerable" contract. `log.md` preserves the removal audit trail. User
edits to derived artifacts are explicitly not protected.

Rejected: leaving orphans in place (bundle drifts from Postgres; `index.md`
no longer lists files that sit in the directory). Rejected: moving orphans
to `_attic/` (redundant with `log.md` + future git-push).

### 9. Synchronous CLI execution
`rag-wiki export` runs synchronously in the CLI process: opens one DB
session, streams `wiki_pages` rows where `status='published'`, writes files
via the storage provider, writes manifest + indexes + log, exits. The worker
queue (ADR-0005) stays for ingestion/synthesis; export is a read-only render
with no per-item async work to enqueue.

Rejected: enqueuing an `export_bundle` job (adds a job row + worker
round-trip for no parallelism benefit). Rejected: `--async` flag (doubles
test surface for a v1 feature that runs fast on a single-tenant deployment).

### 10. Per-page atomic write ordering
For each page, in one step: compute new content hash; if it differs from the
manifest, (a) write/delete the `.md` file, (b) append a `log.md` entry,
(c) update the manifest row. A crash at any point leaves a consistent
partial state — every touched page has file+log+manifest in agreement;
untouched pages remain at the previous run's state. The next run resumes
naturally with no duplicate log entries.

Rejected: batch all files then write manifest once at the end (crash before
manifest write → next run re-detects every page as modified and appends
duplicate log entries). Rejected: temp-dir + atomic rename-swap (orphan
removal across the swap is awkward, disk usage doubles, manifest must live
outside the swap).

### 11. Storage destination via existing `StorageProvider`
The bundle is written through the existing `StorageProvider` abstraction
(ADR-0015): `storage_provider=local` → local directory tree;
`storage_provider=s3` → prefixed keys in SeaweedFS/MinIO. One new setting
`export_output_path` (directory for local, key-prefix for s3), default
`./exports/` (already in `.gitignore`). CLI `--output` overrides the env
var. No new storage abstraction; the bundle simply lives under a different
prefix in the same bucket as source files.

Rejected: a dedicated `export_storage_provider`/`export_s3_*` settings block
(justified only if bundle and source files need different infra — unlikely
in Stage-1). Rejected: CLI-only configuration with no env var (operators
can't bake a default export location into the deployment).

### 12. Module location: `rag_wiki/wiki/export.py`
Single module containing the render loop, front-matter builder, link
rewriter, manifest, and index/log generators. Mirrors the existing flat
`rag_wiki/wiki/` package (`synthesis.py`, `context.py`, `slug.py`). Test
mirror: `tests/wiki/test_export.py`. Split into a subpackage only if it
grows past a few hundred lines.

Rejected: `rag_wiki/wiki/export/` subpackage (premature structure for v1's
two page kinds and one link-rewrite rule). Rejected: `rag_wiki/export/`
top-level package (ADR-0006 scopes wiki storage AND export under the wiki
concern).

## Rationale
- **Export-only scope** keeps blast radius minimal. Every other decision
  (front-matter set, link rewrite, directory layout) is a renderer concern
  that touches exactly one new module and zero existing modules. If OKF
  v0.2 breaks the spec, only `export.py` changes.
- **Inline `[[slug]]` rewrite** is the highest-leverage decision: the
  synthesis template already produces a link graph, just in Obsidian syntax.
  Converting to OKF Markdown links at export time makes the bundle a
  navigable graph for any OKF consumer with zero prompt or DB changes.
- **`type` = entity_type** (not page_kind) aligns with OKF's own example
  usage and gives domain consumers the filter axis they actually care about.
- **Manifest-based `log.md`** is the only way to detect removed pages
  without a DB migration; the manifest is hidden scratch state that doesn't
  pollute the OKF bundle.
- **Per-page atomic writes** make the exporter crash-safe without the disk
  overhead of temp-dir swaps.
- **Synchronous CLI** matches the existing `rag-wiki ingest` pattern and
  avoids queueing overhead for a read-only render.

## Consequences
- `rag-wiki export` moves from stub to working command. The CLI signature
  gains an `--output PATH` flag overriding `export_output_path`.
- `rag_wiki/settings.py` gains one setting (`export_output_path`,
  `Path = Path("./exports")`). `.env.example` gains a commented entry.
- `rag_wiki/wiki/export.py` and `tests/wiki/test_export.py` are new files.
- The synthesis template (`synthesize_entity.j2`) is **unchanged** — it
  keeps emitting `[[slug]]` Obsidian links; the exporter rewrites them.
  Retrieval (ADR-0012) is unaffected: it reads `wiki_pages.content` from
  Postgres, not exported files, so front-matter and link-rewriting never
  reach the section-priority parser.
- OKF v0.1 is a draft. If the spec breaks between v0.1 and v0.2, only
  `export.py` needs revision — the renderer is the single integration point.
- The bundle's link graph is only as complete as the LLM's `[[slug]]`
  emission. If a future synthesis change weakens link emission, the exported
  graph weakens too — but that's a synthesis concern, not an export concern.
- Slug renames (when `entity.name` changes) produce broken links in
  previously-exported bundles. OKF spec tolerates this; the next export
  emits the new slug and `log.md` records the add/remove pair.
- Stage-2 enhancements (git-push distribution, tarball export, OKF-as-Source
  ingest path, MCP serving of OKF concepts) are all additive — none
  rewrites this ADR. The renderer is the seam.
