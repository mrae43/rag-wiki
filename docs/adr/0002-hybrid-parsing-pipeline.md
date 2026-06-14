# ADR-0002: Hybrid document parsing pipeline (lightweight default, optional MinerU path)

## Status
Accepted

## Context
RAG-Anything's headline capability is multimodal document parsing via MinerU:
PDFs and other documents are decomposed into typed chunks — text blocks, tables,
images, and equations — preserving structure and context.

For this project, three approaches were considered:

1. **Full MinerU pipeline** — all content types extracted via MinerU. Closest to
   RAG-Anything, but MinerU is a heavy dependency (large models, GPU recommended),
   which complicates setup and deployment for a portfolio demo.
2. **Lightweight text-first parser only** (e.g. `unstructured`, PyMuPDF, or
   markdown/plaintext) — text and tables as chunks, images stored as references
   only. Fast and deployable anywhere (Railway/Render, no GPU), but loses the
   "multimodal" capability that motivated using RAG-Anything as a reference.
3. **Hybrid** — lightweight parser as the default ingest path, with an optional,
   feature-flagged MinerU-backed path for PDFs that need full multimodal
   decomposition.

## Decision
Use a **hybrid pipeline** (option 3): a lightweight parser is the default and
required dependency; MinerU-based multimodal extraction is an optional,
feature-flagged path.

## Rationale
- **Deployability**: the project must run and be demoable without a GPU or large
  model downloads — important for a portfolio piece reviewers will actually try.
- **Showcases the RAG-Anything-inspired capability without making it load-bearing**:
  the optional path lets the project demonstrate multimodal chunk types (table,
  image, equation) when MinerU is available, satisfying the "all-in-one" framing
  this project is inspired by.
- **Incremental build order**: the lightweight path can be built and demoed first;
  MinerU integration becomes an additive enhancement rather than a blocker.

## Consequences
- The chunk schema (see future ADR on chunk/embedding design) must support
  multiple chunk types (text, table, image, equation) from the start, even though
  only text/table chunks are guaranteed to be populated without MinerU.
- Two code paths exist for ingestion; the lightweight path must remain the
  well-tested, default-on path. The MinerU path is best-effort/optional and
  documented as such.
- Setup docs need to clearly separate "core" requirements from "optional
  multimodal" requirements.
