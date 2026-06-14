# ADR-0010: Fully automated ingestion for v1, with schema support for future review queue

## Status
Accepted

## Context
The original LLM Wiki pattern emphasizes staying involved during ingestion:
reading summaries, checking updates, guiding what gets emphasized. This project
also targets bulk/automated sources (e.g. syncing a Confluence space, Slack
export) for enterprise use, where per-document human review doesn't scale.

Options considered:

1. **Fully automated**: an ingest job runs parse → extract → resolve → embed →
   wiki update end-to-end and commits directly.
2. **Human-in-the-loop review queue**: ingest produces a proposed diff
   (`pending_review` entities/wiki pages) that a human approves, edits, or
   rejects before commit.
3. **Configurable per source**: trusted sources auto-commit, others go to
   review — requires a per-source policy concept in the schema from day one.

## Decision
**Fully automated ingestion (option 1) for v1.** The `wiki_pages` and `entities`
schemas should include a status field (e.g. `status: published | pending_review`)
even though v1 only ever writes `published` — so a review queue (option 2 or 3)
can be added later as an additive change rather than a schema migration that
touches every row.

## Rationale
- **Review-queue UI/workflow is substantial scope on its own** — building it
  before the automated pipeline is proven would risk polishing a workflow around
  a pipeline that isn't yet producing good results.
- **Bulk/enterprise sources need automation regardless** — even with a review
  queue, *some* path needs to handle "sync 500 Confluence pages" without 500
  manual approvals, so the automated pipeline is required either way; building it
  first is the right order.
- **Forward-compatible schema avoids rework**: adding a status column now (even
  if unused beyond one value) means a future review queue is an additive
  feature — new statuses, new endpoints/UI — not a migration touching existing
  data.
- **Explicit trade-off, stated plainly**: this deliberately trades away the
  original pattern's "stay involved, read every summary" philosophy for v1. The
  intent is to recover it via the lint pass (ADR-0008) and `log.md`-style history
  — i.e. you can still review *after the fact*, just not *before commit*.

## Consequences
- `entities`, `relations`, and `wiki_pages` tables include a `status` column from
  the start (default/only value: `published` in v1).
- The lint pass (ADR-0008) becomes the primary mechanism for catching ingestion
  mistakes post-hoc — its output (suggested merges, contradictions) should be
  reviewable, since that's the closest thing to "review" v1 offers.
- A future review-queue feature would add: a `pending_review` status, an
  approval/rejection workflow (API endpoints + minimal UI), and likely a job
  state in the Postgres queue (ADR-0005) for "awaiting approval".
- Audit logging (ADR-0004) should record what an automated ingest *did* (which
  entities created/merged, which wiki pages changed) so that even without
  pre-commit review, there's a clear record for post-hoc review.
