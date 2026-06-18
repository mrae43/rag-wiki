# RagWiki API

The RagWiki HTTP API is a FastAPI application that exposes the automation and
integration surface for the system. It is unauthenticated in v1 and is intended
to run inside a trusted network or behind an existing gateway.

- OpenAPI (Swagger UI): `http://localhost:8000/docs`
- OpenAPI (ReDoc): `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

---

## Quickstart

Start the API with Docker Compose (recommended):

```bash
docker compose up
```

Or run directly with `uvicorn`:

```bash
uv run uvicorn rag_wiki.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Endpoint summary

| Resource | Method | Path | Description |
|----------|--------|------|-------------|
| Health | GET | `/health` | Liveness/readiness probe with DB ping |
| Sources | POST | `/api/v1/sources` | Upload a document and enqueue ingestion |
| Sources | GET | `/api/v1/sources` | List sources (paginated, filterable) |
| Sources | GET | `/api/v1/sources/{id}` | Get a source by id |
| Sources | DELETE | `/api/v1/sources/{id}` | Delete a source and its uploaded file |
| Sources | GET | `/api/v1/sources/{id}/chunks` | List chunks for a source |
| Jobs | GET | `/api/v1/jobs` | List jobs (paginated, filterable) |
| Jobs | GET | `/api/v1/jobs/{id}` | Get a job by id |
| Entities | GET | `/api/v1/entities` | List entities (paginated, filterable) |
| Entities | GET | `/api/v1/entities/{id}` | Get an entity by id |
| Entities | GET | `/api/v1/entities/{id}/relations` | List relations for an entity |
| Entities | GET | `/api/v1/entities/{id}/wiki-page` | Get the entity's primary wiki page |
| Relations | GET | `/api/v1/relations` | List relations (paginated, filterable) |
| Wiki Pages | GET | `/api/v1/wiki-pages` | List wiki pages (paginated, filterable) |
| Wiki Pages | GET | `/api/v1/wiki-pages/{id}` | Get a wiki page by id |
| Wiki Pages | GET | `/api/v1/wiki-pages/slug/{slug}` | Get a wiki page by slug |
| Wiki Pages | GET | `/api/v1/wiki-pages/{id}/mentions` | List entities that mention a page |
| Queries | POST | `/api/v1/queries` | Hybrid retrieval with optional LLM answer |

---

## Pagination

All list endpoints use the same offset/limit envelope:

```json
{
  "items": [...],
  "total": 123,
  "offset": 0,
  "limit": 20
}
```

- `offset` — number of items to skip (default `0`)
- `limit` — page size (default `20`, maximum `100`)

---

## Error format

All errors follow RFC 7807 Problem Details (`application/problem+json`):

```json
{
  "type": "https://rag-wiki.io/errors/source-not-found",
  "title": "Not Found",
  "status": 404,
  "detail": "Source not found: ...",
  "instance": "/api/v1/sources/..."
}
```

Common status codes:

| Status | Meaning |
|--------|---------|
| 400 | Bad request (empty file, invalid metadata, etc.) |
| 404 | Resource not found |
| 413 | Payload too large (upload exceeds limit) |
| 422 | Validation error (invalid JSON body or query params) |
| 500 | Unexpected server error |
| 503 | Service unavailable (e.g. advisory lock exhaustion) |

---

## Examples

### Upload a document

```bash
curl -X POST http://localhost:8000/api/v1/sources \
  -F "file=@/path/to/document.pdf" \
  -F 'metadata={"category": "research"};type=application/json'
```

Response:

```json
{
  "id": "...",
  "file_name": "document.pdf",
  "status": "pending",
  "created_at": "...",
  "updated_at": "...",
  "metadata": {"category": "research"},
  "job_id": "..."
}
```

### Poll an ingestion job

```bash
curl http://localhost:8000/api/v1/jobs/{job_id}
```

### List sources

```bash
curl "http://localhost:8000/api/v1/sources?limit=10&status=pending"
```

### Browse the knowledge graph

```bash
# List entities
curl "http://localhost:8000/api/v1/entities?entity_type=person&name=Alice"

# Entity relations (outgoing, incoming, or both)
curl "http://localhost:8000/api/v1/entities/{id}/relations?direction=outgoing"

# List relations
curl "http://localhost:8000/api/v1/relations?relation_type=mentions"
```

### Read wiki pages

```bash
# By id
curl http://localhost:8000/api/v1/wiki-pages/{id}

# By slug (case-insensitive)
curl http://localhost:8000/api/v1/wiki-pages/slug/alice-smith

# Mentions
curl http://localhost:8000/api/v1/wiki-pages/{id}/mentions
```

### Ask a question

```bash
curl -X POST http://localhost:8000/api/v1/queries \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What did Alice conclude in the 2024 report?",
    "generate_answer": true,
    "seed_entity_ids": ["..."]
  }'
```

Response:

```json
{
  "query": "What did Alice conclude in the 2024 report?",
  "answer": "Alice concluded that ...",
  "retrieval": {
    "seeds": [...],
    "subgraph": [...],
    "wiki_page": {...},
    "seed_chunks": [...],
    "hop1_chunks": [...]
  }
}
```

Set `generate_answer: false` to retrieve structured context without spending
tokens on an LLM answer.

---

## Configuration

The API reads all configuration from environment variables via
`rag_wiki/settings.py`. See `.env.example` for the full list.

Variables that affect the API specifically:

| Variable | Default | Description |
|----------|---------|-------------|
| `API_HOST` | `0.0.0.0` | Bind host |
| `API_PORT` | `8000` | Bind port |
| `UPLOAD_DIR` | `./uploads` | Directory for uploaded source files |
| `UPLOAD_MAX_FILE_SIZE_BYTES` | `104857600` | Maximum upload size (100 MB) |
| `CORS_ORIGINS` | `""` | Comma-separated allowed origins; empty disables CORS |

---

## Out of scope for v1

- Authentication / authorization
- Batch upload
- URL-based ingestion (`POST /sources/from-url`)
- Streaming query answers
- Editable wiki pages
- Mutations on entities/relations/jobs
- Rate limiting
- Real-time job notifications

These are planned for future ADRs and PRs.
