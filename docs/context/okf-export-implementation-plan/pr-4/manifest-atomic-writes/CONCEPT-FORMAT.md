# Concept Notes — PR-4 manifest, atomic writes, and orphan deletion

> Generated after Phase 2 (Socratic Questioning), grounded in the approach the user committed to. This is the mental model, not the implementation.

## Core concept(s)
- **Per-page atomic update:** Treating the file write, log append, and manifest update as one indivisible unit per slug so a crash leaves no partially-updated page.
- **Manifest as diff ground truth:** The hidden manifest records the last exported state; it is the only way to detect removed pages after the corresponding DB row is gone.
- **Idempotent resume:** Because each touched page updates the manifest immediately, a restarted export sees the already-recorded state and skips re-logging unchanged pages.

## Why it matters here
Export is a long-running filesystem/S3 operation. Without per-page atomicity, a crash mid-run produces duplicate `log.md` entries, orphan `.md` files, or pages whose file content disagrees with the manifest. The manifest is the bridge between the DB (source of truth) and the derived bundle; it must stay in lockstep with every file and log entry.

## Mental model / analogy
Like balancing a checkbook one transaction at a time. You record the withdrawal, update the ledger, and mark the check cleared before moving to the next one. If the phone rings, you can resume at exactly the next unprocessed check without redoing earlier ones.

## Common pitfalls
- Writing the manifest only once at the end: a crash causes the next run to re-emit every page as "modified" with duplicate log entries.
- Updating the manifest before deleting an orphan file: the file becomes invisible to the diff but still sits in the bundle.
- Appending a `log.md` entry for every page unconditionally instead of only when the hash changes, inflating the log with no-op entries.
- Forgetting that source-summary pages live under `sources/` while entity pages live under `entities/`; the manifest stores slugs, so path reconstruction must match deletion.

## Related patterns in this domain
- The job queue's `claim_next()` uses `SELECT FOR UPDATE SKIP LOCKED` to make worker claiming atomic; the export loop uses a similar "claim one page, complete its artifacts, record completion" ordering.
- Synthesis uses Postgres advisory locks to keep entity page writes serial; export uses manifest updates to keep bundle state consistent across filesystem/S3 operations.

## Optional further reading
- ADR-0019 §7 (manifest and `log.md`), §8 (orphan deletion), and §10 (per-page atomic write ordering)
