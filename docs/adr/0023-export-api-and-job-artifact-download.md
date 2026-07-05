# ADR-0023: Export API and generalized job-artifact download

## Status
Accepted (revisits ADR-0019 §9)

## Context
ADR-0019 §9 decided that `rag-wiki export` runs synchronously in the CLI and
explicitly rejected enqueuing an `export_bundle` job ("adds a job row + worker
round-trip for no parallelism benefit"). That reasoning held for the CLI path:
export is fast, runs locally, and the operator is the only consumer.

The Interface App (ADR-0021) changes the consumer. A web app cannot shell out
to a CLI on the Backend host; it calls the HTTP API. App-triggered export
needs: (a) an API endpoint to trigger the render, (b) a `job_id` to poll for
progress (ADR-0021 §4), and (c) a download endpoint to fetch the bundle. The
benefit of a job is no longer "parallelism" — it's **not blocking the API
worker** (rendering every wiki page + link rewrite can take minutes on a large
wiki) and **having a pollable `job_id`**. ADR-0019 §9's rejection rationale
doesn't hold for the API path.

## Decision

### 1. Add an `export_bundle` job kind + API endpoints
- `POST /api/v1/export` — enqueues an `export_bundle` job (mirrors
  `POST /sources` from ADR-0013), returns `{job_id}`.
- `GET /api/v1/export/{job_id}` — job status + the run's `progress`
  (ADR-0021 §4) + the artifact key once complete.
- The worker runs the existing ADR-0019 render loop (read `wiki_pages` where
  `status='published'` → write OKF directory → manifest + indexes + log),
  writing the `progress` field between stages.

The CLI `rag-wiki export` path stays unchanged for operator/local use
(ADR-0019 §9's synchronous-CLI reasoning still holds for that case). The
API+job path is additive.

### 2. Generalized `GET /api/v1/jobs/{job_id}/artifact` download
Instead of a per-pipeline `/export/{job_id}/bundle` endpoint, introduce a
generalized artifact-download endpoint: `GET /api/v1/jobs/{job_id}/artifact`.
Any job that produces a file artifact stores its storage key in
`job.result.artifact_key`; this endpoint streams the bytes through the API (via
the `StorageProvider.download()` async iterator, ADR-0015). For the OKF bundle,
the endpoint tarballs the stored directory on the fly and streams
`bundle.tar.gz` (stdlib `tarfile`, no new dep). The same endpoint later serves
PPTX/carousel artifacts from `generate_output` jobs (ADR-0024) — one download
path for all artifact-producing jobs.

Rejected: a per-pipeline `/export/{job_id}/bundle` endpoint — duplicates the
download path for every new artifact-producing job kind. Rejected: the app
reading the bundle directly from the storage provider — couples the app to
ADR-0015's backend and breaks the "app calls Backend API" contract
(CONTEXT.md).

### 3. On-the-fly tarball, one output format
The export job writes the OKF *directory* per ADR-0019 §4 (the CLI path uses
the directory directly for local Obsidian). The download endpoint tarballs
that directory on the fly and streams `bundle.tar.gz`. One output format
(directory); the tarball is a transport encoding, not a separate artifact.
Pre-tarballing during the job + S3 signed URLs is Stage-2 (additive) for large
bundles; at Stage-1 single-tenant scale (hundreds–low thousands of pages)
on-the-fly is fine.

Rejected: pre-tarball during export (doubles storage, two outputs to sync,
CLI/API divergence). Rejected: zip (more universal for non-technical users but
`tar.gz` + stdlib is simpler for v1; zip is additive later).

## Rationale
- **The job path is additive to ADR-0019**, not a reversal of it. ADR-0019 §9's
  synchronous-CLI decision still holds for the CLI; this ADR adds the API+job
  path the Interface App needs. The §9 "Rejected" entry is revisited because
  its reasoning ("no parallelism benefit") didn't consider app-triggered
  exports where the benefit is non-blocking + pollability.
- **Generalized `/jobs/{job_id}/artifact`** means every future
  artifact-producing job (export, generate_output, future report-gen) shares
  one download endpoint — no per-pipeline download routes.
- **On-the-fly tarball** keeps one output format (the OKF directory) and
  avoids doubling storage; the tarball is just a transport encoding for HTTP.

## Consequences
- New job kind `export_bundle`; worker dispatch table gains one entry. The
  render logic stays in `rag_wiki/wiki/export.py` (ADR-0019); the worker calls
  it and writes `progress`.
- New routes: `POST /api/v1/export`, `GET /api/v1/export/{job_id}`,
  `GET /api/v1/jobs/{job_id}/artifact`. The last is general-purpose.
- `Job.result` JSONB conventionally stores `{artifact_key: "...",
  artifact_mime: "application/gzip", ...}` for artifact-producing jobs.
- ADR-0019 §9 is revised: the "Rejected: enqueuing an `export_bundle` job"
  rationale is superseded for the API path; the CLI path's synchronous
  execution is unchanged.
- Stage-2: pre-tarball + S3 signed URL for large bundles (additive); periodic
  scheduled export (additive, uses the existing `scheduled_at` column).
