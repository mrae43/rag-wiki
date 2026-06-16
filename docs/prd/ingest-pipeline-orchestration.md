# PRD: Ingest Pipeline Orchestration + Job Worker

> Step 8 of entity-relation-extraction. Wire the extraction and resolution modules into the ingestion pipeline via a job queue and worker loop.

## Problem Statement

The extraction and resolution modules (`extract_entities`, `resolve_entities`, `merge_entity`) are fully implemented and tested, but they are never invoked. The ingestion pipeline is incomplete: there is no orchestrator that composes `parse → chunk → embed → extract → resolve`, no job worker that claims and executes jobs, and no CLI to enqueue a document. The `jobs` table exists but the queue interface (`enqueue`, `claim_next`, `complete`, `fail`) is empty. The `worker.py` entrypoint is a `SystemExit` stub. The CLI has no commands.

Without the orchestration layer, the end-to-end automated ingestion pipeline (ADR-0010) is still theoretical.

## Solution

Build the missing orchestration layer:

1. **Job queue interface** — implement `enqueue`, `claim_next`, `complete_job`, `fail_job` in `rag_wiki/jobs/__init__.py` using `SELECT FOR UPDATE SKIP LOCKED` on the `jobs` table
2. **Worker loop** — `rag_wiki/worker.py` polls `claim_next`, dispatches by `job_type`, and runs the pipeline
3. **Pipeline orchestrator** — `rag_wiki/ingest/pipeline.py` wires `parse_document` → `Source`/`Chunk` creation → per-chunk embedding → extraction → resolution
4. **CLI command** — `rag-wiki ingest <file_path>` creates a `job` of type `ingest_document`

This closes the loop: a user can drop a file on the CLI, the worker picks it up, and the graph is populated.

## User Stories

1. As a system admin, I want to ingest a document via CLI, so that I can start the pipeline without writing code.
2. As a system admin, I want the worker to poll the job queue and claim jobs atomically, so that multiple workers do not process the same job.
3. As a system admin, I want failed jobs to be retried automatically (up to `max_retries`), so that transient errors (e.g., network timeouts) do not require manual intervention.
4. As a system admin, I want the pipeline to process a document as one transaction (or one source per chunk), so that partial success is visible in the `status` column.
5. As a developer, I want the pipeline orchestrator to be a thin layer (not a deep module), so that the extraction/resolution modules remain testable in isolation.
6. As a developer, I want the worker to log and skip failed chunks without failing the entire job, so that a single malformed chunk does not block the whole document.
7. As a system admin, I want image chunks to be captioned before extraction, so that entities can be extracted from images (ADR-0003).

## Implementation Decisions

### Module structure

| File | Responsibility |
|------|--------------|
| `rag_wiki/jobs/__init__.py` | `enqueue()`, `claim_next()`, `complete_job()`, `fail_job()` — transaction management stays in the caller |
| `rag_wiki/worker.py` | `worker_loop()` — polling, signal handling, provider instantiation, dispatch |
| `rag_wiki/ingest/pipeline.py` | `run_ingest_pipeline()` — parse → source/chunk rows → embed → extract → resolve per chunk |
| `rag_wiki/cli.py` | `rag-wiki ingest <file_path>` — one-shot enqueue via CLI |

### Job queue state machine

- `enqueue(db, job_type, payload, scheduled_at=None)` → creates a `Job(status="pending", attempts=0)` and returns it
- `claim_next(db, job_types=None)` → `SELECT ... WHERE status='pending' AND attempts < max_retries ORDER BY scheduled_at NULLS FIRST, created_at LIMIT 1 FOR UPDATE SKIP LOCKED` → sets `claimed_at` and `worker_id` → returns `Job` or `None`
- `complete_job(job, db)` → `job.status = "completed"`, `job.completed_at = now()`
- `fail_job(job, db, error_message)` → increments `job.attempts`, if `attempts < max_retries` → `job.status = "pending"` (for retry), else → `job.status = "failed"`, `job.error_message = error_message`

### Worker loop

```python
async def worker_loop():
    settings = get_settings()
    chat_provider = get_chat_provider(settings)
    embed_provider = get_embedding_provider(settings)
    stop_event = asyncio.Event()
    # register SIGINT/SIGTERM handlers
    while not stop_event.is_set():
        async with AsyncSessionFactory() as db:
            job = await claim_next(db)
            if job is None:
                await asyncio.sleep(settings.worker_poll_interval_seconds)
                continue
            try:
                if job.job_type == "ingest_document":
                    await run_ingest_pipeline(job, db, chat_provider, embed_provider)
                await complete_job(job, db)
                await db.commit()
            except Exception as exc:
                await fail_job(job, db, str(exc))
                await db.commit()
```

Graceful shutdown: finish the current job, then stop. The current job is not cancelled.

### Pipeline orchestrator

```python
async def run_ingest_pipeline(job, db, chat_provider, embed_provider):
    file_path = job.payload["file_path"]
    source_meta = job.payload.get("source_metadata")

    # 1. Create Source
    source = Source(file_path=file_path, status="processing")
    db.add(source)
    await db.flush()

    # 2. Parse
    parsed_chunks = parse_document(file_path, source_meta)

    # 3. Create Chunk rows
    db_chunks = []
    for i, pc in enumerate(parsed_chunks):
        chunk = Chunk(source_id=source.id, chunk_index=i, chunk_type=pc.chunk_type.value, ...)
        db.add(chunk)
        db_chunks.append(chunk)
    await db.flush()

    # 4. Per-chunk processing
    succeeded = 0
    for chunk in db_chunks:
        try:
            # ImageChunk → caption
            if chunk.chunk_type == "image" and chunk.image_data:
                chunk.text_content = await chat_provider.caption_image(...)

            if chunk.text_content:
                # Embed
                embedding = await embed_provider.embed([chunk.text_content], settings.embedding_model)
                chunk.embedding = embedding[0]

                # Extract
                result = await extract_entities(chunk, chat_provider, settings.llm_model_extraction)
                if not result.entities:
                    logger.debug("chunk %s: no entities extracted", chunk.id)

                # Resolve
                await resolve_entities(
                    candidates=result.entities, chunk=chunk, db=db,
                    chat_provider=chat_provider, embed_provider=embed_provider,
                    job_id=job.id, relations=result.relations,
                )

            chunk.status = "processed"
            succeeded += 1
        except Exception as e:
            logger.error("chunk %s failed: %s", chunk.id, e, exc_info=True)
            chunk.status = "failed"

    # 5. Source status
    if succeeded == 0:
        source.status = "failed"
        raise RuntimeError(f"All chunks failed for source {source.id}")
    else:
        source.status = "processed"
```

### Image chunk handling

- ImageChunks have `image_data` and `image_mime_type`, no `text_content`.
- The pipeline calls `chat_provider.caption_image(image_data, image_mime_type, settings.llm_model_caption)` and stores the caption as `chunk.text_content`.
- `chunk.chunk_type` remains `"image"` for provenance.
- The chunk then flows through the same embed → extract → resolve pipeline as a text chunk.

### Error handling

- **Per-chunk exception**: caught in the loop, `chunk.status = "failed"`, logged, pipeline continues.
- **All chunks fail**: `source.status = "failed"`, `RuntimeError` raised, worker `fail_job` retries.
- **Job-level exception** (e.g., `parse_document` crashes): caught in worker loop, `fail_job`, `db.commit`.

### CLI command

```python
# rag_wiki/cli.py

async def _ingest_command(file_path: str):
    from rag_wiki.db.session import AsyncSessionFactory
    from rag_wiki.jobs import enqueue
    async with AsyncSessionFactory() as db:
        job = await enqueue(db, "ingest_document", payload={"file_path": file_path})
        await db.commit()
        print(f"Job {job.id} enqueued")

if __name__ == "__main__":
    # argparse dispatch
    pass
```

The CLI assumes it runs in the same filesystem context as the worker (e.g., Docker bind mount). In v1, host-path mapping is out of scope.

### Dependencies

- `worker.py` imports `AsyncSessionFactory` from `rag_wiki.db.session`
- `worker.py` imports `get_chat_provider` and `get_embedding_provider` from `rag_wiki.providers`
- `pipeline.py` imports `extract_entities`, `resolve_entities` from `rag_wiki.graph`
- `cli.py` imports `AsyncSessionFactory` and `enqueue` from `rag_wiki.jobs`

## Testing Decisions

- **Unit tests for job queue**: `test_enqueue`, `test_claim_next`, `test_fail_job_retries` with the `db` fixture
- **Integration test**: `tests/ingest/test_pipeline.py` — `test_ingest_pipeline_roundtrip`
  - Creates a temp `.md` file with `tmp_path`
  - Uses `FakeChatProvider` with `response_map` for extraction and resolution tools
  - Uses `FakeEmbeddingProvider` (may need `embedding_dimensions` override or fake vector length fix)
  - Calls `run_ingest_pipeline` directly (or enqueues + worker loop)
  - Asserts: Source(status="processed"), Chunks with embeddings, Entities, Relations, chunk_entities links

## Out of Scope

- **API endpoint for ingestion** — CLI-only in v1
- **Periodic lint pass** — separate subsystem per ADR-0008
- **Wiki page synthesis** — handled in separate PRD
- **Host-to-container path mapping** — assume CLI runs inside container for v1
- **Multiple concurrent workers** — single worker loop in v1
- **Worker metrics / health checks** — observability is flagged but not decided
- **Chunk embedding for retrieval** — chunk embedding is handled inline in the pipeline, but the retrieval subsystem itself is out of scope

## Further Notes

- `source.status` does not have a `"partial"` state in v1 — per-chunk `status` already provides granularity. A `"partial"` status would require a migration and is not needed until a user asks for it.
- `resolve_entities()` already handles advisory locks, chunk-entity linking, and relation creation. The pipeline must not duplicate any of that.
- `FakeEmbeddingProvider` currently returns 1536-dim vectors but `embedding_dimensions` is 3072. The integration test may need to override the settings or patch the fake.
- The worker commit is `await db.commit()` after the try/except block. The `fail_job` call mutates the job object inside the same transaction, so the commit persists both the failure status and any partially processed rows. Since the worker runs in its own transaction, partial results are visible in the DB.
