# PRD-003: OKF Wiki Export

## Problem Statement

The `rag-wiki export` CLI command has been a stub since ADR-0006 landed — `rag_wiki/cli.py:95` prints "Export not yet implemented" and exits 1. Users who want to browse their knowledge wiki in file-based tools (Obsidian, IDEs, git) have no way to obtain a rendered artifact. The wiki lives exclusively in Postgres, which is the correct source-of-truth arrangement (ADR-0006), but the derived file export that the architecture always promised does not exist.

The Open Knowledge Format (OKF v0.1), published by Google Cloud in June 2026, provides a vendor-neutral spec for representing organizational knowledge as markdown concept files with YAML front-matter and markdown links. Adopting OKF as the export format gives us producer/consumer independence, a navigable link graph derived from the existing `[[slug]]` synthesis output, and two reserved navigation aids (`index.md`, `log.md`) that map cleanly onto the wiki page model.

## Solution

A synchronous `rag-wiki export` CLI command that renders published `wiki_pages` rows to an OKF-compliant directory bundle. The exporter is a pure renderer — it reads Postgres, writes files through the existing `StorageProvider` abstraction (ADR-0015), and never feeds back. Front-matter is built from existing DB columns (`entity_type`, `title`, `description`, `synthesized_at`), `[[slug]]` Obsidian links are regex-rewritten to OKF markdown links inline, and a hidden manifest supports crash-safe `log.md` diffing and orphan-file deletion. The bundle mirrors Postgres exactly on every run.

## User Stories

1. As a system operator, I want to run a single `rag-wiki export` command that produces a complete, navigable OKF bundle of all published wiki pages, so that I can browse my knowledge wiki from a file system.

2. As an Obsidian user, I want each wiki page to be a `.md` file with YAML front-matter and correct markdown links, so that Obsidian's graph view, backlinks, and search work out of the box.

3. As a system operator, I want the bundle to contain an `index.md` that lists every concept grouped by page kind (entity vs source-summary), so that I can navigate the bundle without knowing specific slugs.

4. As a system operator, I want a `log.md` that records what changed between export runs (added, modified, removed pages), so that I have a human-readable changelog in the bundle.

5. As a system operator, I want deleted wiki pages to be removed from the bundle automatically on the next export, so that the bundle never contains stale files that no longer exist in Postgres.

6. As a system operator, I want the export to be crash-safe — a failure partway through leaves previously-exported pages intact and only logs the interrupted change — so that a partial export never corrupts the bundle.

7. As a developer, I want the export output path to be configurable via a single env var (`EXPORT_OUTPUT_PATH`) with a sensible default (`./exports/`), so that I can bake the location into any deployment.

8. As a developer, I want export to use the same `StorageProvider` abstraction as source-file uploads, so that on an S3-backed deployment (`STORAGE_PROVIDER=s3`), the export lands in the same bucket under an `exports/` prefix.

9. As a developer, I want the export command to accept an `--output PATH` flag that overrides the env var, so that one-off exports to arbitrary locations work without config changes.

10. As a developer, I want the export to run synchronously in the CLI process (no job queue round-trip), so that the operation completes in a single command without a running worker.

11. As a developer, I want the exporter module at `rag_wiki/wiki/export.py` with a mirrored test file at `tests/wiki/test_export.py`, so that the code follows the existing project layout conventions.

## Implementation Decisions

### 1. Export format only — Postgres remains the source of truth

OKF is adopted **only** as the format emitted by `rag-wiki export`. The `wiki_pages` table is unchanged; `content` stays free-form LLM-authored markdown. The exporter is a pure renderer — it reads Postgres, writes files, and never feeds back. ADR-0006's "Postgres is SoT, file export is derived" contract is untouched.

The `WikiPage` model (`rag_wiki/db/models/wiki.py`) already has all columns needed by the exporter: `slug`, `title`, `content`, `entity_id`, `status`, `synthesized_from_sources`, `synthesized_at`, and the `WikiPageEntity` join table supports slug→name map construction for link rewriting.

### 2. Front-matter field set

Six fields emitted for every concept file:

| Field | Source | Notes |
|---|---|---|
| `type` | `entity.entity_type` (entity pages) / `"rag-wiki:source-summary"` (source pages) | OKF's only formally-required field |
| `title` | `wiki_pages.title` | |
| `description` | `entity.description` (entity pages, omit if NULL) / first-paragraph excerpt (~200 chars, word-boundary, ellipsis) for source pages | Render-time heuristic; no schema change |
| `timestamp` | `wiki_pages.synthesized_at` | Knowledge valid-time, not export-run time |
| `page_kind` | `"entity"` / `"source_summary"` | Disambiguates the two page kinds orthogonal to `type` |
| `resource` | `{api_base_url}/entities/{entity_id}` (entity) / `{api_base_url}/sources/{source_id}` (source) | OKF-reserved field; reuses `mcp_api_url` as the base |

No internal Postgres UUIDs (`uuid`, `entity_id`, `slug`) are leaked into front-matter — the `resource` URL carries the round-trip pointer.

### 3. `type` = entity_type, not page_kind

For entity pages, `type` carries the LLM-extracted domain category (`person`, `organization`, `concept`, … — free text per `rag_wiki/prompts/constants.py:13`). The orthogonal page-kind axis lives in the separate `page_kind` field. This matches OKF's own example and lets domain consumers filter by real-world concept type.

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

Concept ID = file path minus `.md` (e.g. `entities/acme-corp-12345678`). Slugs are `name-<first8 uuid>` (per `rag_wiki/wiki/slug.py`), stable across regenerations as long as `entity.name` doesn't change. No sub-grouping by `entity_type` — LLM re-classification on future ingest would shift Concept IDs.

### 5. Inline rewrite of `[[slug]]` → OKF Markdown links

The entity synthesis template (`rag_wiki/prompts/templates/synthesize_entity.j2:13,27`) already instructs the LLM to emit `[[entity-slug]]` Obsidian-style wiki links for all related entities, forward-referenced or not. The exporter regex-rewrites every `[[slug]]` inline to `[Label](../entities/slug.md)`, resolving `Label` from a slug→name map built from `entities` + `wiki_page_entities`. Links to entities without a wiki page are emitted anyway — OKF spec §9 explicitly tolerates broken/forward links as "not-yet-written knowledge." Body content stays LLM-faithful; only link syntax changes. Zero prompt changes.

### 6. `index.md` at root + per-directory

Root `index.md` lists all concepts grouped by `page_kind`. `entities/index.md` and `sources/index.md` list their children. Each entry is a markdown link + title + one-line `description` (progressive disclosure). Built from `wiki_pages` rows; honors OKF's reserved-filename intent.

### 7. `log.md` via hidden manifest diff

A hidden `.rag-wiki-export-manifest.json` at the bundle root stores `slug → content_hash` for the last successful export. On each run, the exporter diffs the new hash set against the manifest to compute added/modified/removed and appends a dated entry to a root `log.md`. The manifest is scratch state (a dotfile), not an OKF concept — consumers ignore it.

### 8. Orphan-file deletion

When the manifest-diff detects a removed page (slug in manifest, not in DB), the exporter deletes the corresponding `.md` file. The bundle always mirrors Postgres exactly. `log.md` preserves the removal audit trail. User edits to derived artifacts are explicitly not protected.

### 9. Synchronous CLI execution

`rag-wiki export` runs synchronously in the CLI process: opens one DB session, streams `wiki_pages` rows where `status='published'`, writes files via the storage provider, writes manifest + indexes + log, exits. The worker queue stays for ingestion/synthesis; export is a read-only render with no per-item async work to enqueue.

### 10. Per-page atomic write ordering

For each page, in one step: compute new content hash; if it differs from the manifest, (a) write/delete the `.md` file, (b) append a `log.md` entry, (c) update the manifest row. A crash at any point leaves a consistent partial state — every touched page has file+log+manifest in agreement; untouched pages remain at the previous run's state. The next run resumes naturally with no duplicate log entries.

### 11. Storage destination via existing `StorageProvider`

The bundle is written through the existing `StorageProvider` abstraction (ADR-0015): `storage_provider=local` → local directory tree; `storage_provider=s3` → prefixed keys in SeaweedFS/MinIO. One new setting `export_output_path` (directory for local, key-prefix for s3), default `Path("./exports")` (already in `.gitignore`). CLI `--output` overrides the env var.

Both existing provider implementations (`rag_wiki/storage/local.py`, `rag_wiki/storage/s3.py`) are ready. The `StorageProvider` protocol (`rag_wiki/storage/base.py`) defines `upload()` and `delete()` which the exporter will use.

### 12. Module location: `rag_wiki/wiki/export.py`

Single module containing the render loop, front-matter builder, link rewriter, manifest, and index/log generators. Mirrors the existing flat `rag_wiki/wiki/` package (`synthesis.py`, `context.py`, `slug.py`). Test mirror: `tests/wiki/test_export.py`.

## Testing Decisions

### What makes a good test

- Tests should exercise the **external contract** — what files the exporter produces given specific DB state — not implementation details such as front-matter dict construction or regex internals.
- The test should run against a real SQLite or Postgres database with seeded `wiki_pages`, `entities`, and `wiki_page_entities` rows, then assert the output bundle structure and content.
- The manifest and log.md behavior (diff, append, orphan deletion) must be tested separately across multiple export runs.

### Modules to test

| Test file | What it tests | Prior art |
|---|---|---|
| `tests/wiki/test_export.py` | Full export pipeline: creates bundle with correct directory layout, front-matter fields, `[[slug]]` rewrite, `index.md` at root + per-directory, `log.md` entries, manifest read/write, orphan-file deletion, crash-safe partial state, `--output` flag override, S3 key prefix construction | `tests/wiki/test_synthesis.py` — similar DB-seeded integration test pattern |

### Manual validation

Before first real use:

1. `uv run rag-wiki export` — confirm a complete OKF bundle appears in `./exports/`
2. `uv run rag-wiki export --output /tmp/my-bundle` — confirm output path override works
3. Open root `index.md` in a markdown viewer — confirm all concepts are listed with links
4. Open an entity `.md` file — confirm front-matter, correct `[[slug]]`→markdown link rewrite, and a navigable link graph
5. `uv run rag-wiki export` again — confirm `log.md` shows no changes on a re-run
6. Delete a wiki page in DB, re-export — confirm the `.md` file is removed and `log.md` records the removal

## Out of Scope

- **OKF-as-Source ingest path** — reading an OKF bundle back in to create/update wiki pages. This is a separate feature requiring a new ADR.
- **git-push distribution** — auto-committing the bundle to a git repo after export. Additive Stage-2 enhancement.
- **Tarball export** — `rag-wiki export --archive` producing a `.tar.gz` of the bundle. Additive enhancement.
- **MCP serving of OKF concepts** — serving individual `.md` files via MCP resources. Additive Stage-2 enhancement.
- **Import/round-trip** — reading exported `.md` files back into the system. Not a goal for v1.
- **Diff-based partial exports** — exporting only changed pages. The v1 exporter always renders the full published set; the manifest diff is only for `log.md` and orphan deletion.
- **Wiki page revisions table** — no `wiki_page_revisions` table exists yet; `log.md` is the only changelog mechanism for v1.

## Further Notes

- ADR-0019 records the full rationale for every sub-decision, including rejected alternatives. Refer to it during implementation when a decision needs re-examination.
- The `export_output_path` default (`./exports/`) is already in `.gitignore` (`exports/`). No gitignore change needed.
- The existing CLI export stub at `rag_wiki/cli.py:95` must be replaced with the real implementation and gain an `--output` flag.
- `rag_wiki/settings.py` needs one new field: `export_output_path: Path = Path("./exports")`. Both `.env.example` files need a commented `EXPORT_OUTPUT_PATH` entry.
- The synthesis templates (`synthesize_entity.j2` and `synthesize_source_summary.j2`) are unchanged — they keep emitting `[[slug]]` Obsidian links; the exporter rewrites them.
- Retrieval (ADR-0012) is unaffected: it reads `wiki_pages.content` from Postgres, not exported files.
- OKF v0.1 is a draft. If the spec breaks between v0.1 and v0.2, only `export.py` needs revision.
- Slug renames (when `entity.name` changes) produce broken links in previously-exported bundles. OKF spec tolerates this; the next export emits the new slug and `log.md` records the add/remove pair.
- Stage-2 enhancements (git-push, tarball, OKF-as-Source, MCP resources) are all additive — none rewrites this PRD or the ADR. The renderer is the seam.
