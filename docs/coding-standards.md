# Coding Standards

This document defines the coding standards and best practices for this project.
All contributors (human or LLM agent) must follow these conventions.
The goal is code that is readable, debuggable, and maintainable by someone
encountering it for the first time.

---

## 1. Documentation

### 1.1 Module docstrings
Every Python file starts with a module-level docstring. One short paragraph
explaining what this module does and what it does NOT do (scope boundary).

```python
"""
ragwiki.graph.extraction
------------------------
Extracts entities and relations from a chunk of text using the configured
LLM provider. Does NOT perform entity resolution or write to the database —
callers are responsible for passing results to the resolver.
"""
```

### 1.2 Class docstrings
Every class gets a docstring. State the responsibility of the class, and if it
is a protocol/interface, say so explicitly.

```python
class LLMProvider(Protocol):
    """
    Protocol defining the LLM operations the system needs.

    All LLM calls in this codebase go through an implementation of this
    protocol. Concrete implementations live in ragwiki.providers.*
    Callers depend on this interface, never on a concrete implementation.
    """
```

### 1.3 Function/method docstrings
Every public function and method gets a docstring. Use the following format:

```python
async def extract_entities(
    chunk: Chunk,
    provider: LLMProvider,
) -> list[ExtractedEntity]:
    """
    Extract entities and relations from a single chunk via the LLM.

    Args:
        chunk: The chunk to process. Must have a non-empty text_content field.
        provider: The LLM provider used for extraction calls.

    Returns:
        A list of ExtractedEntity objects. May be empty if no entities are found.

    Raises:
        LLMProviderError: If the provider call fails after retries.
        ValueError: If chunk.text_content is empty.

    Note:
        This function does not perform entity resolution.
        Callers should pass results to EntityResolver before writing to the DB.
    """
```

Rules:
- `Args` — one line per parameter, type already in signature so just describe meaning/constraints.
- `Returns` — what is returned, including edge cases (empty list, None).
- `Raises` — every exception the caller needs to handle.
- `Note` — scope boundary or gotchas, keep it short.
- Private functions (`_prefixed`) can have a shorter docstring but still need one
  if the logic is non-obvious.

### 1.4 Inline comments
Use inline comments to explain **why**, not **what**. The code says what it does;
the comment explains the reason.

```python
# Bad — restates the code
chunk_size = 512  # set chunk size to 512

# Good — explains the reasoning
chunk_size = 512  # pgvector's default HNSW index performs well up to ~512-dim;
                  # larger chunks risk exceeding the context window during resolution
```

---

## 2. Error handling

### 2.1 Never swallow exceptions silently

```python
# Bad
try:
    result = await provider.complete(prompt)
except Exception:
    pass

# Good
try:
    result = await provider.complete(prompt)
except LLMProviderError as exc:
    logger.error("LLM completion failed", chunk_id=chunk.id, error=str(exc))
    raise
```

### 2.2 Define domain exceptions
Each sub-package has its own exception hierarchy rooted in a base exception.
Never raise bare `Exception` or `RuntimeError` from domain code.

```python
# ragwiki/exceptions.py
class RagWikiError(Exception):
    """Base exception for all ragwiki errors."""

class LLMProviderError(RagWikiError):
    """Raised when an LLM provider call fails after retries."""

class EntityResolutionError(RagWikiError):
    """Raised when entity resolution cannot make a merge/new decision."""

class IngestError(RagWikiError):
    """Raised when a source document cannot be processed."""
```

### 2.3 Be specific in except clauses
Catch the narrowest exception type possible. `except Exception` is only
acceptable at the top-level job runner boundary (to prevent a worker crash from
taking down all jobs) and must always log the full traceback.

```python
# At job runner boundary only
try:
    await run_job(job)
except Exception as exc:
    logger.exception("Unhandled error in job", job_id=job.id, exc_info=exc)
    await fail_job(job.id, error=str(exc))
```

### 2.4 Graceful degradation on optional paths
The optional MinerU path (ADR-0002) must never crash the ingestion pipeline.
Wrap it so a failure falls back to the lightweight parser and records a warning.

```python
try:
    chunks = await mineru_parser.parse(source)
except IngestError as exc:
    logger.warning(
        "MinerU parsing failed, falling back to lightweight parser",
        source_id=source.id,
        error=str(exc),
    )
    chunks = await lightweight_parser.parse(source)
```

### 2.5 Always include context in error messages
Error messages must include enough context to diagnose the problem without
attaching a debugger.

```python
# Bad
raise IngestError("Failed to parse document")

# Good
raise IngestError(
    f"Failed to parse document: source_id={source.id!r} "
    f"path={source.path!r} parser=lightweight"
) from exc
```

---

## 3. Typing

### 3.1 Type-annotate everything
All function signatures must have full type annotations — parameters and return
type. Use `from __future__ import annotations` at the top of every file.

```python
from __future__ import annotations

async def resolve_entity(
    candidate: ExtractedEntity,
    existing: list[Entity],
    provider: LLMProvider,
) -> Entity | None:
    ...
```

### 3.2 Use Pydantic models for data that crosses boundaries
Any data that enters or leaves the system (API requests/responses, LLM outputs,
job payloads) must be a Pydantic model. Do not use raw dicts.

```python
# Bad
async def enqueue(job: dict) -> None: ...

# Good
class IngestJobPayload(BaseModel):
    source_id: UUID
    parser: Literal["lightweight", "mineru"] = "lightweight"

async def enqueue(job: IngestJobPayload) -> None: ...
```

### 3.3 Use `TypeAlias` for readability
```python
from typing import TypeAlias

EntityId: TypeAlias = UUID
ChunkId: TypeAlias = UUID
```

---

## 4. Formatting & style

### 4.1 Formatter and linter: ruff only
Run `ruff format` for formatting and `ruff check` for linting before every
commit. No other formatter (black, isort, flake8) is used.

Configuration lives in `pyproject.toml`:
```toml
[tool.ruff]
line-length = 88
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "ANN"]
```

### 4.2 Indentation
4 spaces. No tabs. ruff enforces this — do not override.

### 4.3 Line length
88 characters (ruff default). Long strings (SQL, prompts) are the one exception;
they may exceed this if breaking them would hurt readability.

### 4.4 Imports
Order: standard library → third-party → internal. One blank line between groups.
ruff's `I` rule set enforces this automatically.

```python
from __future__ import annotations

import asyncio
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel

from ragwiki.db.models import Entity
from ragwiki.providers.base import LLMProvider
```

### 4.5 Naming
- `snake_case` for variables, functions, modules.
- `PascalCase` for classes.
- `SCREAMING_SNAKE_CASE` for module-level constants.
- Prefix internal helpers with `_`.
- Name async functions/methods the same as sync equivalents would be — do not
  add `async_` prefix. The `async def` keyword is enough.

---

## 5. Async conventions

### 5.1 All I/O is async
Database calls (SQLAlchemy async), LLM provider calls, and file I/O during
ingestion are all async. Never use synchronous I/O (requests, psycopg2 sync) in
the async code paths.

### 5.2 Do not block the event loop
CPU-bound work (e.g. parsing large PDFs with PyMuPDF) must be offloaded to a
thread pool:
```python
import asyncio
from functools import partial

result = await asyncio.get_event_loop().run_in_executor(
    None, partial(pymupdf_parse, path)
)
```

### 5.3 Propagate cancellation
Do not catch `asyncio.CancelledError`. Let it propagate so the event loop can
clean up properly.

---

## 6. Logging

### 6.1 Structured logging throughout
Use `structlog` for all logging. Every log call includes at minimum the relevant
domain ID(s) as keyword arguments.

```python
import structlog

logger = structlog.get_logger(__name__)

# Always bind domain context
logger.info("entity resolved", entity_id=entity.id, merged_into=existing.id)
logger.warning("resolution skipped", entity_id=candidate.id, reason="low similarity")
logger.error("provider call failed", chunk_id=chunk.id, attempt=attempt, error=str(exc))
```

### 6.2 Log levels
- `DEBUG` — internal state useful only during active development.
- `INFO` — normal operations a production operator might want to know about
  (job started, job completed, entity created, wiki page updated).
- `WARNING` — something unexpected but recoverable (fallback path taken,
  retry triggered).
- `ERROR` — something failed and requires attention (job failed after retries,
  provider down).
- Never use `print()` in production code paths.

### 6.3 No secrets in logs
Never log API keys, tokens, or user content (document text). Log IDs and
metadata only.

---

## 7. Database

### 7.1 All schema changes go through Alembic
Never use `CREATE TABLE` or `ALTER TABLE` ad-hoc. Every schema change is an
Alembic migration with a descriptive message:
```
alembic revision --autogenerate -m "add status column to wiki_pages"
```

### 7.2 Explicit column selection
Never use `SELECT *` in queries. Always select the columns you need.

### 7.3 Keep SQL readable
For complex queries (recursive CTEs for graph traversal, `SELECT FOR UPDATE SKIP
LOCKED` for job claiming), write the SQL in a clearly named constant or helper,
with a comment explaining the query's purpose.

```python
# Claim the next available job atomically. SKIP LOCKED prevents multiple
# workers from blocking on the same row.
CLAIM_JOB_SQL = """
    UPDATE jobs
    SET status = 'claimed', claimed_at = now(), worker_id = :worker_id
    WHERE id = (
        SELECT id FROM jobs
        WHERE status = 'pending'
        ORDER BY created_at
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING *
"""
```

### 7.4 Use transactions explicitly
Any operation that writes to more than one table must be wrapped in a single
transaction. Do not rely on autocommit behavior.

---

## 8. Testing

### 8.1 Test file mirrors source structure
```
ragwiki/graph/extraction.py     →  tests/graph/test_extraction.py
ragwiki/providers/openai.py     →  tests/providers/test_openai.py
```

### 8.2 One assertion concept per test
Each test checks one behavior. Name tests as `test_<what>_<condition>`:
```python
async def test_extract_entities_returns_empty_for_blank_chunk(): ...
async def test_resolve_entity_merges_when_similarity_above_threshold(): ...
async def test_ingest_job_falls_back_to_lightweight_on_mineru_failure(): ...
```

### 8.3 Mock LLM providers in unit tests
Never make real LLM calls in unit or integration tests. Use a `FakeLLMProvider`
that returns deterministic responses.

### 8.4 Cover the error paths
At least one test per function must exercise the failure/exception path, not just
the happy path.

---

## 9. Configuration

### 9.1 All config from environment variables
No hardcoded URLs, model names, or API keys anywhere in source code. Use
`pydantic-settings`:
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    llm_provider: Literal["openai", "anthropic"] = "openai"
    llm_model_extraction: str = "gpt-4o-mini"
    llm_model_wiki_synthesis: str = "gpt-4o"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    class Config:
        env_file = ".env"
```

### 9.2 Provide `.env.example`
Every environment variable must appear in `.env.example` with a comment
explaining what it controls and any valid values.

---

## 10. Git hygiene

### 10.1 Commit message format
```
<type>(<scope>): <short summary>

[optional body explaining why, not what]
```
Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`.
Example: `feat(ingest): add MinerU optional parsing path`

### 10.2 One logical change per commit
Do not mix a feature addition and a formatting cleanup in one commit.

### 10.3 Never commit secrets
`.env` is in `.gitignore`. Only `.env.example` (no real values) is committed.