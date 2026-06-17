# ADR-0011: MinerU as primary parser (post-roadmap refactoring plan)

## Status

Deferred — to be implemented after the main roadmap (hybrid retrieval + FastAPI
API) is complete. This ADR records the decided plan so it is not lost or
re-decided.

## Context

ADR-0002 established a hybrid parsing pipeline: lightweight parsers (PyMuPDF,
unstructured, simple) as the default, with MinerU as an optional path. This
was the right call for v1 — it kept the system demoable without GPU and
unblocked the rest of the pipeline.

Now that the core pipeline (parse → chunk → embed → extract → resolve → wiki
synthesis) and upcoming retrieval + API layers are being built against the
lightweight parsers, the question is: **what happens when we upgrade parsing
quality?**

MinerU (`magic-pdf`) supports PDF, images, DOCX, PPTX, and XLSX — it overlaps
with every current parser. Its output is richer (layout regions, reading order,
cross-page tables, LaTeX formulas) and produces higher-quality extraction,
especially for complex scanned documents and multi-modal content.

## Decision

When the main roadmap is complete, refactor the parsing layer so that **MinerU
becomes the primary parser for all file types it supports** (PDF, DOCX, PPTX,
XLSX, images), with the current lightweight parsers as fallbacks when MinerU is
not installed.

Specific decisions:

1. **Auto-upgrade (B2)**: when `rag-wiki[mineru]` is installed, MinerU
   intercepts all file types it handles. No per-source configuration needed.
   Lightweight parsers are only used when MinerU is absent.
2. **Flat schema mapping**: MinerU's richer output gets mapped into the same
   `ParsedChunk` union (TextChunk, TableChunk, ImageChunk). Structural metadata
   (reading order, layout regions) goes into the `metadata` dict, not into new
   chunk subtypes. An `EquationChunk` type will be added at this point for
   LaTeX/formula content, with a corresponding migration.
3. **Parser registry refactor**: the current `if/elif` flat dispatch in
   `parser.py` gets replaced with a priority-based parser registry. Each parser
   registers which MIME types it handles and its priority. MinerU entries get
   higher priority; fallback entries get lower. This replaces both the
   `MIME_DISPATCH` dict and the `source_metadata["parser"]` override.
4. **ChunkType extended**: `EQUATION = "equation"` is added to the `ChunkType`
   enum as part of this refactor, along with the DB migration. Equations are
   captioned-to-text per ADR-0003 (LaTeX serialised, then embedded in the same
   vector space).

## Mental models

### Current parsing layer (pre-refactor)

```
file_path
  │
  ├─ mimetype lookup
  │    MIME_DISPATCH = {application/pdf → "pdf",
  │                     text/plain    → "simple",
  │                     text/markdown → "simple"}
  │
  ├─ optional source_metadata["parser"] override
  │
  └─ if/elif flat dispatch:
       "pdf"          → parse_pdf()         (PyMuPDF)
       "ocr"          → OCR via PyMuPDF
       "simple"       → parse_simple()       (txt/md)
       else           → parse_unstructured()  (unstructured lib)

Output:  ParsedChunk = TextChunk | TableChunk | ImageChunk
         ChunkType  = TEXT | TABLE | IMAGE
```

### Post-refactor parsing layer

```
file_path
  │
  ├─ parser registry (priority-based):
  │    ┌──────────┬────────┬─────────────────────────────────┐
  │    │ Parser   │ Prio   │ MIME types                      │
  │    ├──────────┼────────┼─────────────────────────────────┤
  │    │ MinerU   │ high   │ PDF, DOCX, PPTX, XLSX, images   │
  │    │ PyMuPDF  │ medium │ application/pdf                  │
  │    │ unsruct. │ medium │ DOCX, HTML, etc.                 │
  │    │ simple   │ low    │ text/plain, text/markdown        │
  │    └──────────┴────────┴─────────────────────────────────┘
  │
  ├─ if MinerU import fails → fall through to next priority
  │
  └─ optional per-source force override (debug only)

Output:  ParsedChunk = TextChunk | TableChunk | ImageChunk | EquationChunk
         ChunkType  = TEXT | TABLE | IMAGE | EQUATION
```

### What changes, what doesn't

```
                     ┌─────────────┐
                     │ parse_       │
                     │ document()   │          ← REWRITES: parser registry,
                     └──────┬───────┯            priority dispatch, MinerU adapter
                            │
                     ┌──────▼───────┐
                     │ ParsedChunk  │          ← EXTENDS: + EquationChunk
                     └──────┬───────┯
                            │
              ┌─────────────┼─────────────┐
              │             │             │
        ┌─────▼─────┐ ┌────▼────┐ ┌──────▼──────┐
        │ caption   │ │ embed   │ │ extract     │    ← UNCHANGED
        │ (images)  │ │         │ │ entities    │
        └─────┬─────┘ └────┬────┘ └──────┬──────┘
              │             │             │
              └─────────────┼─────────────┘
                            │
                     ┌──────▼───────┐
                     │ resolve      │          ← UNCHANGED
                     │ entities     │
                     └──────┬───────┘
                            │
                     ┌──────▼───────┐
                     │ wiki         │          ← UNCHANGED
                     │ synthesis    │
                     └──────────────┘
```

The refactor touches **only** the dispatch layer (`parse_document()`) and the
chunk schema (`ChunkType` + `EquationChunk`). Everything downstream already
operates on the `ParsedChunk` union contract and is unaffected.

## Rationale

- **Quality is the point**: MinerU produces significantly better extraction for
  complex documents. Once the system is end-to-end functional, upgrading parser
  quality is the highest-leverage improvement — better chunks cascade into
  better embeddings, better entities, better wiki pages.
- **Deferred, not abandoned**: GPU requirements and heavy model downloads would
  have blocked the roadmap. Building against lightweight parsers first means the
  retrieval and API layers are already working when MinerU arrives.
- **Same pipeline contract**: MinerU and lightweight parsers both produce
  `ParsedChunk` objects. Downstream stages (embed, extract, resolve, synthesize)
  are completely untouched by this refactor — only `parse_document()` and
  `ChunkType` change.
- **Registry over if/elif**: a priority-based registry makes the "prefer MinerU,
  fall back to lightweight" logic explicit and extensible. Adding another parser
  later is a registration, not a code change.

## Consequences

- ADB-0002's "optional, feature-flagged path" language is replaced: MinerU
  becomes the primary parser when available. The lightweight parsers become the
  fallback path. ADR-0002 should be updated to reflect this change in status.
- The refactor must maintain the property that the system runs without MinerU
  (no GPU, no large model downloads). All existing lightweight parsers remain
  installed and working.
- `EquationChunk` and the `EQUATION` chunk type require a DB migration
  (`ALTER TYPE ... ADD VALUE`) and a code change to `schemas.py`,
  `ChunkType`, the `chunks` table, and the caption-then-embed path.
- The parser registry refactor should preserve the ability to force a specific
  parser via source metadata (for debugging), but the primary mechanism is
  priority-based auto-selection.
- This ADR is **deferred**: it should NOT be implemented until the retrieval
  pipeline and FastAPI API are working end-to-end. Premature MinerU integration
  risks blocking the roadmap on GPU availability and heavy dependency setup.
  