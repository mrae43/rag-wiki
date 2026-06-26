# PRD: Google AI Embeddings Provider

## Problem Statement

The ingest and retrieval pipelines use an `OpenAIProvider` for both chat completions and text embeddings. The current `.env` configuration points to NVIDIA NIM (`integrate.api.nvidia.com/v1`) for chat, but a recent decision to use `gemini-embedding-2` requires embeddings to be served from Google AI's API instead. These are different endpoints with incompatible auth schemes (NVIDIA uses `Authorization: Bearer`; Google AI uses `x-goog-api-key`). Without a separate embedding provider, embeddings cannot be generated without contaminating the chat pipeline with Google-specific auth, or the embedding pipeline with NVIDIA-specific model routing.

PDF ingestion also fails in the Docker container because PyMuPDF (`fitz`) is not installed — the image was built before `pymupdf` was added to `pyproject.toml`.

## Solution

A new `GoogleAIProvider` implementing the existing `EmbeddingProvider` protocol, using direct `httpx` HTTP calls to Google AI's `batchEmbedContents` REST API. Chat stays exclusively on NVIDIA NIM via the existing `OpenAIProvider`. The two providers target different base URLs and auth schemes with zero code overlap — the `EmbeddingProvider` protocol enforces the boundary naturally (no `ChatProvider` methods on the Google AI side).

The Docker image is rebuilt to include PyMuPDF, enabling PDF ingestion.

## User Stories

1. As a user ingesting a PDF document, I want text, tables, and images to be extracted and embedded using `gemini-embedding-2` via Google AI, so that chunk embeddings use the correct model without interfering with chat traffic.
2. As a developer configuring the system, I want to set `LLM_EMBEDDING_PROVIDER=googleai` in `.env`, so that embeddings route to Google AI while chat remains on NVIDIA NIM.
3. As a developer configuring embedding behaviour, I want to set `EMBEDDING_TASK_TYPE=RETRIEVAL_DOCUMENT` to optimise chunk embeddings for being found during retrieval, and `EMBEDDING_TASK_TYPE=RETRIEVAL_QUERY` when embedding user queries, so that both sides of the retrieval distance computation benefit from task-specific vector tuning.
4. As a developer running the system, I want the system to authenticate with Google AI via the `x-goog-api-key` header (not `Authorization: Bearer`), so that authentication is compatible with the Google AI API's security model.
5. As a developer, I want the `GoogleAIProvider` to implement only `EmbeddingProvider` and not `ChatProvider`, so that it is impossible to accidentally route chat traffic through Google AI.
6. As a developer, I want embeddings to accept up to 100 texts per batch request via `batchEmbedContents`, so that multi-text embedding operations in the pipeline (e.g., resolution scoring) use a single API call.
7. As a developer, I want the `outputDimensionality` parameter sent when `SEND_DIMENSIONS=true`, so that the returned embedding vector matches the configured `EMBEDDING_DIMENSIONS` value and is compatible with the pgvector column width.
8. As a developer, I want the `taskType` configurable via the `EMBEDDING_TASK_TYPE` env var, so that the embedding model's output is optimised for the correct use case (document storage or query) without code changes.
9. As a developer, I want the Docker image rebuilt with PyMuPDF installed, so that PDF documents can be parsed during ingestion.
10. As a developer, I want error responses from the Google AI API wrapped in `LLMProviderError`, so that the error handling contract is consistent across all providers.

## Implementation Decisions

### Module structure

- `rag_wiki/providers/googleai.py` — new file containing `GoogleAIProvider(EmbeddingProvider)`
- `rag_wiki/providers/__init__.py` — register `"googleai"` in `EMBEDDING_PROVIDERS` factory dict; `CHAT_PROVIDERS` unchanged
- `rag_wiki/settings.py` — add `gemini_api_key` and `embedding_task_type` config fields

### GoogleAIProvider interface

The provider implements exactly one method matching the `EmbeddingProvider` protocol:

```python
async def embed(self, texts: list[str], model: str) -> list[list[float]]
```

It does NOT implement `complete()`, `caption_image()`, or any other `ChatProvider` method. This is enforced by the protocol — `GoogleAIProvider` only inherits `EmbeddingProvider`.

### API details

- Endpoint: `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents`
- Auth: `x-goog-api-key` header set to the value of `GEMINI_API_KEY` (also passed as `?key=` query param for reliability)
- Request body structure for N texts:

```json
{
  "requests": [
    {
      "model": "models/gemini-embedding-2",
      "content": {"parts": [{"text": "text 1"}]}
    },
    {
      "model": "models/gemini-embedding-2",
      "content": {"parts": [{"text": "text 2"}]}
    }
  ]
}
```

- When `SEND_DIMENSIONS=true` and `embedding_dimensions > 0`, each request includes:
  ```json
  "embedContentConfig": {
    "outputDimensionality": 3072,
    "taskType": "RETRIEVAL_DOCUMENT"
  }
  ```
- Response body:
  ```json
  {
    "embeddings": [
      {"values": [0.001, -0.002, ...]},
      {"values": [-0.003, 0.004, ...]}
    ]
  }
  ```
- Result: `[emb["values"] for emb in data["embeddings"]]`

### Settings additions

```python
gemini_api_key: str | None = None
embedding_task_type: str | None = "RETRIEVAL_DOCUMENT"
```

`embedding_task_type` accepts any Google AI task-type enum value:
`TASK_TYPE_UNSPECIFIED`, `RETRIEVAL_QUERY`, `RETRIEVAL_DOCUMENT`, `SEMANTIC_SIMILARITY`, `CLASSIFICATION`, `CLUSTERING`, `QUESTION_ANSWERING`, `FACT_VERIFICATION`, `CODE_RETRIEVAL_QUERY`.

Default is `RETRIEVAL_DOCUMENT` because chunk embeddings are always documents being stored for retrieval. Query embeddings at query time should be set to `RETRIEVAL_QUERY` at the call site (future work).

### Responsibility boundary

`GoogleAIProvider` implements only `EmbeddingProvider`, not `ChatProvider`. The provider registry in `providers/__init__.py` enforces this at the factory level:

```python
EMBEDDING_PROVIDERS = {"openai": OpenAIProvider, "googleai": GoogleAIProvider}
CHAT_PROVIDERS = {"openai": OpenAIProvider}
```

No `ChatProvider` method exists on `GoogleAIProvider`. If a caller attempts to pass it where a `ChatProvider` is expected, the type checker will reject it.

### Error handling

API errors (HTTP 4xx/5xx) are caught and wrapped in `LLMProviderError` with the model name in the message, consistent with `OpenAIProvider.embed()`.

### Docker

Rebuild the `api` image: `docker compose build --no-cache api`. PyMuPDF is already declared in `pyproject.toml` — the missing dependency in the running container is a stale image issue.

### Dependency

`httpx` is added as an explicit dependency in `pyproject.toml` (currently only a transitive dep via FastAPI). Making it direct ensures it is always available to the new provider.

### Configuration

`.env` changes:

```
LLM_EMBEDDING_PROVIDER=googleai
EMBEDDING_DIMENSIONS=3072
SEND_DIMENSIONS=true
EMBEDDING_TASK_TYPE=RETRIEVAL_DOCUMENT
```

No `LLM_EMBEDDING_BASE_URL` is needed — the Google AI endpoint is fixed per-model.

## Testing Decisions

### What makes a good test

- Tests the external behaviour of `GoogleAIProvider.embed()`: input texts → correct HTTP request construction → correct embedding vector extraction from mock API response
- Tests the boundary: the provider raises `LLMProviderError` on non-2xx responses
- Tests config wiring: `send_dimensions`, `embedding_dimensions`, `embedding_task_type` are correctly reflected in the request body
- Tests the `googleai` registration: `get_embedding_provider(Settings(llm_embedding_provider="googleai"))` returns a `GoogleAIProvider`
- Does NOT make real HTTP calls — uses `httpx` mock transport or `respx` for transport-layer interception

### Modules to test

- `rag_wiki/providers/googleai.py` — full unit test suite with mocked HTTP transport
- `rag_wiki/providers/__init__.py` — factory resolution for `"googleai"`

### Prior art

- `tests/providers/test_openai.py` — tests `OpenAIProvider` with mocked `openai.AsyncClient`; the Google AI tests should follow the same pattern (mock HTTP at the transport layer instead of the client)
- `tests/conftest.py` — shared fixtures and settings helpers

New test file: `tests/providers/test_googleai.py`

## Out of Scope

- Implementing `ChatProvider` on `GoogleAIProvider` — chat stays on NVIDIA NIM
- Implementing `EmbeddingProvider` for Anthropic or any provider other than Google AI
- Migration from `openai` to `googleai` in production — both providers coexist; users toggle via `LLM_EMBEDDING_PROVIDER`
- Multi-model embedding routing (e.g., per-chunk-type model selection) — all embeddings use the same model
- Query-time `RETRIEVAL_QUERY` task type override — the query planner already handles this; the provider simply passes through whatever `EMBEDDING_TASK_TYPE` is configured
- Batch-size splitting (sending >100 texts in multiple requests) — Google AI free tier supports 100 per call; the ingest pipeline embeds one chunk at a time

## Further Notes

The Google AI Developer API free tier provides 1500 requests per day for `gemini-embedding-2`, which is sufficient for development workloads. Production deployments should monitor RPD and consider upgrading to a paid tier or routing through a proxy.

`httpx` is chosen over `aiohttp` because it is already a transitive dependency of FastAPI. Making it explicit avoids dependency drift. If `httpx` is not present, `GoogleAIProvider.__init__` should raise a clear `LLMProviderError` at construction time, not at call time.