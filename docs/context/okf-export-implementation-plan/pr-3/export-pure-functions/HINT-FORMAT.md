# Build Hints — PR-3 `export.py` pure functions

> These exist to build your mental model of the *shape* of the solution — not to be copied as the implementation. If a snippet below could be pasted in and made to work with minor edits, it's too close to a solution — rewrite it as pseudocode or strip it down further.

## Similar pattern already in this codebase
- **Location:** `rag_wiki/wiki/slug.py:generate_slug()`
  - What to notice: this is the single source of truth for turning a name + UUID into a slug. The exporter does not invent its own slug logic; it uses this to label links and to stay consistent with synthesis.
- **Location:** `rag_wiki/wiki/synthesis.py:_source_slug()`
  - What to notice: source-summary pages generate slugs from `source.file_name` + `source.id`. The exporter needs the same mapping for source-summary slugs, but reads `synthesized_from_sources` to resolve the source.
- **Location:** `rag_wiki/db/models/wiki.py`
  - What to notice: `WikiPage.entity_id` is `NULL` for source-summary pages. That column is the discriminator for `page_kind` and for how you build the `resource` URL.

## Illustrative shape (pseudocode, not working code)
```text
function build_slug_name_map(db_session):
    result = empty map
    for each published wiki page (joined with entity if present):
        if page has entity:
            result[page.slug] = entity.name
        else:
            # source-summary page
            resolve first source_id in synthesized_from_sources
            result[page.slug] = source.file_name
    return result

function rewrite_links(content, slug_map):
    for each occurrence of [[some-slug]] in content:
        label = slug_map.get(some-slug, some-slug)   # fallback to slug itself
        emit a markdown link using label and relative path
    return transformed content
```

## Gotchas flagged during Phase 2
- Source-summary pages have `entity_id=NULL`; don't assume every page has an entity.
- `synthesized_from_sources` can be empty for a source-summary page; guard before building the `resource` URL.
- Links to missing slugs must still emit markdown links with the slug as the label — OKF explicitly tolerates broken/forward links.
- The regex should be anchored and conservative; avoid rewriting `[[...]]` that appears inside code fences or image syntax if possible.

## Where to look if stuck
- ADR-0019 §5 for the exact link-rewrite rule and §2 for the front-matter field set.
- `rag_wiki/prompts/templates/synthesize_entity.j2` to see how `[[slug]]` links are emitted during synthesis.
