# ADR-0024: Generated Output pipeline (carousel, PPTX)

## Status
Accepted

## Context
ADR-0019 covers the **Knowledge Bundle** — a faithful, no-LLM, deterministic
render of every wiki page as OKF markdown. The Interface App (ADR-0021) needs a
different kind of output: a **Generated Output** — a *synthesized presentation
artifact* (carousel, PowerPoint) produced from wiki content via LLM.
CONTEXT.md distinguishes the two: a Knowledge Bundle renders pages as-is; a
Generated Output synthesizes new slide content from retrieved pages.

This ADR records the pipeline for Generated Outputs: input shape, synthesis,
rendering, and delivery. It shares the job+download infrastructure from
ADR-0023 but is a separate job kind because the pipeline logic (LLM synthesis)
is fundamentally different from the OKF render (deterministic).

## Decision

### 1. New `generate_output` job kind, separate from `export_bundle`
A `generate_output` job takes a topic/query + optional seed entity IDs,
retrieves wiki context, synthesizes a slide spec via LLM, and renders to the
requested format(s). Separate from `export_bundle` (ADR-0023) because the
pipeline is LLM-synthesis, not faithful render. Both share the `jobs` table,
the `progress` JSONB (ADR-0021 §4), and the `GET /api/v1/jobs/{job_id}/artifact`
download endpoint (ADR-0023 §2).

### 2. Input mirrors `/queries`; reuses the retrieval pipeline
Job params: `{topic: str, seed_entity_ids: [uuid]?, formats: ["pptx",
"carousel"], ...}`. The worker calls `retrieve(topic, seed_entity_ids)`
(ADR-0012) — the same retrieval the query endpoint uses — to gather a
`RetrievalResult`. The output is grounded in the wiki (not the LLM's
parametric knowledge) and carries cited sources/entities.

Rejected: free-text topic with no retrieval (LLM generates from parametric
knowledge — risks hallucination, no cited sources, defeats the purpose).
Rejected: entity/page selection only (no free-text query — loses the
"summarize what the wiki knows about X" flow).

### 3. One slide spec, multiple format renders
The worker makes LLM calls to synthesize a **slide spec** (JSON: slides with
title, bullets, speaker notes, cited entity/source IDs) from the
`RetrievalResult`. The spec is rendered to each requested format:
- **PPTX** — `python-pptx` (well-maintained, MIT). One `.pptx` file artifact.
- **HTML carousel** — Jinja2 template, swipeable cards. One `.html` file
  artifact (the app can render inline or download).

Both render from the *same* spec, so the synthesis LLM calls happen once
regardless of how many formats are requested. Each rendered artifact is stored
via the `StorageProvider` (ADR-0015) under `outputs/{job_id}/`;
`job.result.artifact_keys = {pptx: "...", carousel: "..."}`.

Rejected: generalize ADR-0019's export into a multi-format renderer with
`format: okf|pptx|carousel` — conflates faithful-render (no LLM) with
LLM-synthesis pipeline logic. Two separate job kinds sharing download infra is
cleaner than one pipeline with two modes.

### 4. No edit-before-render in v1
The slide spec is internal to the job. The app requests an output, polls
`GET /jobs/{id}` for progress, and downloads the rendered artifact when
complete. The user does not preview/edit slides before rendering in v1.

Stage-2: expose the slide spec in `job.result.slides` so the app can preview +
edit + re-render (an additive `POST /api/v1/outputs/{job_id}/rerender`
endpoint, no pipeline rewrite).

### 5. Formats for v1: PPTX + HTML carousel
PPTX covers "take it offline into PowerPoint/Google Slides/Keynote"; HTML
carousel covers "view it in the app." Both render from one spec. PDF,
reveal.js, Google Slides direct export are Stage-2 renderers from the same
spec (additive — a new renderer module per format).

## Rationale
- **Separate job kind from `export_bundle`** — the pipeline logic is genuinely
  different (LLM synthesis vs deterministic render). Forcing them into one
  multi-format renderer conflates two modes; two job kinds sharing the
  download endpoint is the cleaner seam.
- **Reuse `retrieve()`** — the output is wiki-grounded with cited sources for
  free; the only new Backend code is the slide-synthesis prompt + the two
  renderers. No new retrieval/graph code.
- **One spec, many formats** — synthesis LLM calls are the expensive part;
  doing them once and rendering multiple formats from the result avoids
  redundant cost.
- **PPTX + carousel for v1** — the two formats cover "download a deck" and
  "view inline"; PDF and others reuse the same spec in Stage-2.

## Consequences
- New job kind `generate_output`; worker dispatch table gains one entry.
- New module `rag_wiki/outputs/` for the synthesis prompt + renderers:
  `synthesize.py` (slide-spec LLM call), `render_pptx.py` (`python-pptx`),
  `render_carousel.py` (Jinja2). Test mirror in `tests/outputs/`.
- New prompt template `rag_wiki/prompts/templates/synthesize_slides.j2`.
- New deps in `pyproject.toml`: `python-pptx`. (Jinja2 is already present.)
- `POST /api/v1/outputs` route (enqueue) + the shared
  `GET /api/v1/jobs/{job_id}/artifact` download (ADR-0023 §2) — no new
  download endpoint.
- ADR-0019 (Knowledge Bundle) is untouched; this ADR adds a parallel output
  pipeline.
- Stage-2: slide-spec preview + edit + re-render, PDF/reveal.js renderers,
  scheduled/templated outputs — all additive.
