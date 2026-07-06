# Build Hints — PR-4 manifest, atomic writes, and orphan deletion

> These exist to build your mental model of the *shape* of the solution — not to be copied as the implementation. If a snippet below could be pasted in and made to work with minor edits, it's too close to a solution — rewrite it as pseudocode or strip it down further.

## Similar pattern already in this codebase
- **Location:** `rag_wiki/jobs/__init__.py:claim_next()`
  - What to notice: the worker claims a job, does the work, and then marks it complete — never the reverse. The export loop mirrors this "do work, then record state" ordering per page.
- **Location:** `rag_wiki/storage/base.py`
  - What to notice: `with_temp_file()` wraps download-temp-cleanup. The new `write_text`/`read_text`/`list_keys` methods (PR-2) will be called here; understand their contract before wiring them into the atomic loop.

## Illustrative shape (pseudocode, not working code)
```text
# Load previous state
manifest = load hidden manifest from storage
new_state = empty map

# Process current pages
for each published wiki page:
    rendered = render_page(page)          # uses PR-3 pure functions
    hash = hash_content(rendered)

    if manifest.get(page.slug) != hash:
        write file to storage
        append change record to log
        new_state[page.slug] = hash
    else:
        new_state[page.slug] = manifest[page.slug]

# Clean up removed pages
for each slug in manifest but not in new_state:
    delete file from storage
    append removal record to log
    # do not add to new_state

# Persist log and new manifest
flush log to storage
save new_state as the manifest
```

## Gotchas flagged during Phase 2
- Per-page ordering matters: file → log → manifest. Reverse it and a crash creates duplicates or hidden orphans.
- `log.md` is append-only; never rewrite the whole file from scratch.
- The manifest is hidden scratch state (`.rag-wiki-export-manifest.json`), not an OKF concept; consumers ignore it.
- A crash during cleanup should still leave the bundle consistent: every untouched slug remains at its previous state.

## Where to look if stuck
- ADR-0019 §7, §8, and §10 for the rationale behind manifest diffing, orphan deletion, and atomic-write ordering.
- `rag_wiki/storage/local.py` and `rag_wiki/storage/s3.py` once PR-2 lands the new text-IO methods; their error handling shapes what the exporter must catch.
