# E2E Integration Test Guidance — `tests/ingest/test_pipeline.py`

> **Audience:** Coding agents tasked with writing `test_e2e_full_pipeline` and related E2E tests.
> **Goal:** Validate that the complete assembly works — document in, wiki pages out, query retrieves them.
> **Not the goal:** Re-test component internals already covered by unit/graph/retrieval/wiki tests.

---

## 1. What "E2E" means in this codebase

The existing tests in `tests/ingest/test_pipeline.py` stop at **job enqueue**. They assert that `run_ingest_pipeline()` writes `Job` rows to the database, but never execute those jobs. The synthesis functions (`synthesize_entity_page`, `synthesize_source_summary`) and the query path are untested end-to-end.

A true E2E test must:

1. Upload a file via `POST /api/v1/sources` (or call `run_ingest_pipeline()` directly)
2. Drain every enqueued job by calling the synthesis functions directly (not via the worker loop)
3. Assert wiki pages exist in the database with meaningful content
4. Call `POST /api/v1/queries` and assert the answer relates to the ingested document

---

## 2. The session lifecycle problem — read this first

### Why the standard `db` fixture breaks E2E

`conftest.py`'s `db` fixture wraps everything in an outer `conn.begin()` transaction with `join_transaction_mode="create_savepoint"`. Calls to `await db.commit()` inside `run_ingest_pipeline()` and the synthesis functions release savepoints — data is visible within the same session, but **the outer transaction rolls back at fixture teardown**.

This is fine for unit tests. For E2E, you need data written by the ingest pipeline to be visible when you call synthesis functions, and data written by synthesis to be visible when you query. The savepoint trick cannot bridge calls that open their own sessions (which the worker loop does, but which you will not use directly).

### The `persistent_db` fixture

Create a **function-scoped** fixture that commits normally and truncates at teardown:

```python
# In tests/ingest/test_pipeline.py or a local conftest.py

@pytest.fixture
async def persistent_db(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """A session that commits for real — required for E2E tests that span
    ingest → synthesis → query in a single test function."""
    session = AsyncSession(bind=engine, expire_on_commit=False)
    yield session
    # Teardown: truncate all tables so the next test starts clean.
    async with session.begin():
        for table in reversed(Base.metadata.sorted_tables):
            await session.execute(text(f"TRUNCATE {table.name} CASCADE"))
    await session.close()
```

**Do not use the `db` fixture for E2E tests. Do not use `client` (it is wired to `db`). Build a separate HTTP client wired to `persistent_db` or call functions directly.**

---

## 3. Running synthesis jobs in-process

Do **not** start the `worker_loop()`. It polls a live database with its own `AsyncSessionFactory` sessions, uses signal handlers, and sleeps between iterations — it cannot be driven from a test.

Instead, claim and dispatch jobs directly using the same functions the worker calls:

```python
from rag_wiki.jobs import claim_next, complete_job, fail_job
from rag_wiki.wiki.synthesis import synthesize_entity_page, synthesize_source_summary

SYNTHESIS_DISPATCH = {
    "synthesize_entity": synthesize_entity_page,
    "synthesize_source_summary": synthesize_source_summary,
}

async def drain_synthesis_jobs(
    db: AsyncSession,
    chat_provider: ChatProvider,
    embed_provider: EmbeddingProvider,
    max_jobs: int = 20,
) -> int:
    """Claim and execute all pending synthesis jobs. Returns number of jobs run.

    Call this after run_ingest_pipeline() to complete the pipeline end-to-end.
    Raises if any job fails (so test failures are loud, not silent).
    """
    ran = 0
    worker_id = "test-worker"
    for _ in range(max_jobs):
        job = await claim_next(db, worker_id=worker_id)
        if job is None:
            break
        if job.job_type not in SYNTHESIS_DISPATCH:
            await fail_job(job, db, f"unknown job type: {job.job_type}")
            await db.commit()
            raise AssertionError(f"Unexpected job type in E2E test: {job.job_type}")
        handler = SYNTHESIS_DISPATCH[job.job_type]
        try:
            if job.job_type == "synthesize_entity":
                await handler(job, db, chat_provider, embed_provider)
            else:
                await handler(job, db, chat_provider)
            await complete_job(job, db)
            await db.commit()
        except Exception as exc:
            await fail_job(job, db, str(exc))
            await db.commit()
            raise
        ran += 1
    return ran
```

Place `drain_synthesis_jobs` in the test file or a local `conftest.py`. It is test-only infrastructure.

---

## 4. Provider configuration for E2E

### Embedding — use hash-based vectors, not zeros

`FakeEmbeddingProvider` returns all-zero vectors. This breaks retrieval: every chunk gets the same embedding, so vector-seed results are meaningless.

Create `_DeterministicEmbeddingProvider` that produces stable, non-identical vectors:

```python
import hashlib

class _DeterministicEmbeddingProvider:
    """Produces non-zero, stable vectors so retrieval produces meaningful seeds."""

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        dims = get_settings().embedding_dimensions
        result = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            # Repeat digest bytes to fill dims, then normalize to [-1, 1]
            raw = list(digest * (dims // len(digest) + 1))[:dims]
            vector = [(b / 127.5) - 1.0 for b in raw]
            result.append(vector)
        return result
```

Use `_DeterministicEmbeddingProvider` for E2E tests. Keep `FakeEmbeddingProvider` (zeros) for unit tests that don't exercise retrieval.

### Chat — canned response map

The synthesis functions call `chat_provider.complete()` without a tool schema (they send a system prompt and expect `response.content`). The extraction/resolution steps use tool calls with `response_map`.

Your E2E `FakeChatProvider` must handle **both** call patterns in one instance:

```python
EXTRACTION_JSON = json.dumps({
    "entities": [
        {
            "surface_form": "Apple Inc.",
            "canonical_name": "Apple Inc.",
            "entity_type": "organization",
            "description": "A technology company.",
        },
        {
            "surface_form": "Tim Cook",
            "canonical_name": "Tim Cook",
            "entity_type": "person",
            "description": "CEO of Apple Inc.",
        },
    ],
    "relations": [
        {"source_idx": 0, "target_idx": 1, "relation_type": "CEO_OF"}
    ],
})

MERGE_JSON = json.dumps({"decision": "new", "reasoning": "No existing match."})

def make_e2e_chat_provider() -> FakeChatProvider:
    """Chat provider covering extraction, resolution, and synthesis call shapes."""
    return FakeChatProvider(
        response_map={
            # Entity + relation extraction (tool call)
            "extract_entities_and_relations": EXTRACTION_JSON,
            # Entity resolution (tool call)
            "merge_decision": MERGE_JSON,
            # Synthesis calls have no tools — FakeChatProvider.complete()
            # returns response.content = "fake-completion-for-<model>"
            # which is non-empty, so synthesize_entity_page will write a page.
        }
    )
```

`FakeChatProvider.complete()` already returns `content="fake-completion-for-{model}"` when no tool matches. That non-empty string is enough for synthesis to write a `WikiPage` row. No extra wiring needed for synthesis calls.

---

## 5. The HTTP client for E2E

The standard `client` fixture is wired to `db` (the rollback fixture). For E2E you need a client wired to `persistent_db`:

```python
@pytest.fixture
async def e2e_client(persistent_db: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    from rag_wiki.db.session import get_db

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield persistent_db

    fastapi_app.dependency_overrides[get_db] = _override
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()
```

Use `e2e_client` for the upload and query steps.

---

## 6. The canonical E2E test

```python
@pytest.mark.e2e
async def test_e2e_full_pipeline(
    persistent_db: AsyncSession,
    e2e_client: AsyncClient,
    single_chunk_txt: str,          # existing fixture — small known-content file
) -> None:
    """
    Full pipeline: upload → ingest → synthesize → query.

    Assertions:
      - Source row created
      - Chunks embedded and stored
      - Entities and relations extracted
      - Wiki pages written (entity page + source summary)
      - Query returns non-empty answer mentioning content from the document
    """
    chat = make_e2e_chat_provider()
    embed = _DeterministicEmbeddingProvider()

    # ── Step 1: Upload and ingest ──────────────────────────────────────────
    with open(single_chunk_txt, "rb") as f:
        response = await e2e_client.post(
            "/api/v1/sources",
            files={"file": ("test.txt", f, "text/plain")},
        )
    assert response.status_code == 201
    source_id = response.json()["id"]

    # Ingest pipeline runs synchronously in-process via the API handler,
    # OR enqueues an ingest_document job. Check which pattern your API uses.
    #
    # If the API enqueues "ingest_document" and returns immediately:
    ingest_job = await claim_next(persistent_db, worker_id="test-worker")
    assert ingest_job is not None
    assert ingest_job.job_type == "ingest_document"
    await run_ingest_pipeline(ingest_job, persistent_db, chat, embed)
    await complete_job(ingest_job, persistent_db)
    await persistent_db.commit()

    # ── Step 2: Drain synthesis jobs ───────────────────────────────────────
    jobs_run = await drain_synthesis_jobs(persistent_db, chat, embed)
    assert jobs_run >= 1, "Expected at least one synthesis job to run"

    # ── Step 3: Assert wiki pages exist ────────────────────────────────────
    pages = (await persistent_db.execute(select(WikiPage))).scalars().all()
    assert len(pages) >= 1

    entity_pages = [p for p in pages if p.entity_id is not None]
    source_pages = [p for p in pages if p.entity_id is None]
    assert len(entity_pages) >= 1, "Expected at least one entity wiki page"
    assert len(source_pages) >= 1, "Expected a source summary page"

    for page in pages:
        assert page.content, f"Wiki page {page.slug!r} has empty content"
        assert page.synthesized_at is not None

    # ── Step 4: Assert entities and relations ──────────────────────────────
    entities = (await persistent_db.execute(select(Entity))).scalars().all()
    assert len(entities) >= 2  # Apple Inc. + Tim Cook from EXTRACTION_JSON

    entity_names = {e.name for e in entities}
    assert "Apple Inc." in entity_names
    assert "Tim Cook" in entity_names

    relations = (await persistent_db.execute(select(Relation))).scalars().all()
    assert len(relations) >= 1

    # ── Step 5: Query and assert answer ───────────────────────────────────
    query_response = await e2e_client.post(
        "/api/v1/queries",
        json={"query": "Who is the CEO of Apple Inc.?", "generate_answer": True},
    )
    assert query_response.status_code == 200
    body = query_response.json()

    assert body["answer"] is not None, "Expected LLM-generated answer"
    assert body["retrieval"]["chunks"] or body["retrieval"]["context"], \
        "Expected retrieval to return context chunks"
```

---

## 7. Assertions — what to check and what to skip

### Check these

| Assertion | Why |
|---|---|
| `WikiPage` rows exist with non-empty `content` | Core value of the system |
| `WikiPage.synthesized_at` is set | Confirms synthesis ran, not just enqueue |
| `Entity` names match the extraction JSON you supplied | Confirms extraction + resolution wired correctly |
| At least one `Relation` row | Confirms relation extraction path ran |
| Query response `status 200` | Basic liveness |
| `body["answer"]` is not None and not empty | LLM answer path ran |
| `body["retrieval"]` contains chunks | Hybrid retrieval returned something |

### Skip these in E2E

| What | Why |
|---|---|
| Exact `content` wording of wiki pages | `FakeChatProvider` returns `"fake-completion-for-<model>"` — testing exact text is circular |
| Semantic quality of the answer | The LLM is faked; semantic assertions belong in eval harnesses, not pytest |
| Error path branches (all-chunks-fail, partial-fail) | Already covered in existing `test_pipeline.py` |
| Component internals (chunk splitting logic, advisory lock retries) | Already covered in unit tests |

---

## 8. Second test — two documents, entity resolution

This test validates that ingesting a second document referencing the same entity **resolves to the existing entity** rather than creating a duplicate, and that the entity wiki page is updated.

```python
@pytest.mark.e2e
async def test_e2e_entity_resolution_across_documents(
    persistent_db: AsyncSession,
    e2e_client: AsyncClient,
    single_chunk_txt: str,
    tmp_path: Path,
) -> None:
    """
    Two documents mention Apple Inc. Entity resolution must merge them.
    Wiki page update must reflect both source IDs.
    """
    # Configure resolution to return "merge" on the second ingest
    merge_json_first = json.dumps({"decision": "new", "reasoning": "First occurrence."})
    merge_json_second = json.dumps({"decision": "merge", "candidate_id": "<will be filled>"})

    # ... (see note below)
```

> **Note for agent:** Entity resolution requires the `merge_decision` tool to return the UUID of the existing entity as `candidate_id`. At test time you won't know the UUID in advance. Two patterns to handle this:
>
> 1. **Post-hoc assertion only** — use `decision: "new"` for both ingests (two entities created), then assert `WikiPage.synthesized_from_sources` contains both source IDs after the second synthesis run. This is simpler and sufficient to validate the coalescing path in `synthesize_entity_page._merge_duplicate_jobs`.
>
> 2. **Dynamic response map** — after the first ingest, query the DB for the entity UUID, construct the merge JSON dynamically, and swap the `response_map` before running the second ingest. More complex but validates the full resolution path.
>
> Start with option 1. Add option 2 only if the merge resolution path is not otherwise tested.

---

## 9. Test marking and CI separation

Mark every E2E test:

```python
@pytest.mark.e2e
async def test_e2e_full_pipeline(...):
    ...
```

Register the marker in `pyproject.toml` (or `pytest.ini`):

```toml
[tool.pytest.ini_options]
markers = [
    "e2e: end-to-end tests that commit to a real database and run the full pipeline",
]
```

Run E2E tests separately from the fast suite:

```bash
# Fast suite (unit + integration, rolled-back DB)
pytest -m "not e2e"

# E2E suite (requires running Postgres)
pytest -m e2e --timeout=60
```

E2E tests are slow by design (multiple DB writes, multiple LLM calls through fake providers). Do not attempt to make them fast by mocking the DB or skipping commits.

---

## 10. File layout

```
tests/
  conftest.py                     # existing — do not modify
  ingest/
    conftest.py                   # existing — add persistent_db, e2e_client, 
    |                             # _DeterministicEmbeddingProvider, 
    |                             # make_e2e_chat_provider, drain_synthesis_jobs here
    test_pipeline.py              # existing — add E2E tests at the bottom,
                                  # clearly separated with a comment block:
                                  # # ── E2E Tests ──────────────────────────────
```

Do not put `persistent_db` in the root `conftest.py` — it commits for real and would silently break unit tests that assume rollback isolation if they accidentally used it.

---

## 11. Checklist before submitting

- [ ] `persistent_db` fixture exists, commits normally, truncates at teardown
- [ ] `e2e_client` wired to `persistent_db`, not `db`
- [ ] `_DeterministicEmbeddingProvider` used (not `FakeEmbeddingProvider` with zeros)
- [ ] `drain_synthesis_jobs` drains all pending synthesis jobs and raises on failure
- [ ] `@pytest.mark.e2e` on every E2E test function
- [ ] Assertions cover: `WikiPage` rows, `Entity` rows, `Relation` rows, query `200`, non-null `answer`
- [ ] No assertions on exact wiki page content wording
- [ ] No re-testing of error paths already in `test_pipeline.py`
- [ ] E2E tests pass in isolation (`pytest -m e2e -x`) against a clean test database