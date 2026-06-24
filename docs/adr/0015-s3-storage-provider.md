# ADR-0015: S3-compatible storage via a StorageProvider abstraction

## Status
Accepted

## Context
The system stores raw source documents (PDFs, images, articles) during ingestion.
Currently, uploaded files are written to a local filesystem directory (`upload_dir`)
and referenced by absolute path in the `sources.file_path` column. This works for
single-instance development but creates problems for any multi-host deployment:

- Files written by the API container are not visible to the worker container
  unless both share a volume mount.
- The Docker Compose dev setup uses a `.:/app` bind mount, which happens to make
  files visible to both containers, but is not a production pattern.
- Horizontal scaling (multiple API or worker replicas) breaks immediately —
  a file uploaded to one API instance is invisible to another.

A self-hosted S3-compatible store (SeaweedFS) solves the shared-storage problem
while keeping the deployment self-contained — no cloud dependency, no external
bucket, just another container in the compose file.

Five design choices needed to be made:
1. Direct S3 integration vs. a provider abstraction layer.
2. Cloud S3 vs. a self-hosted S3-compatible store.
3. Full S3 URI vs. opaque key in the source record.
4. Async S3 client library.
5. Where in the pipeline the S3 upload occurs.

## Decision

### 1. Provider abstraction (StorageProvider protocol)
Introduce a `StorageProvider` protocol in the style of the existing
`ChatProvider`/`EmbeddingProvider` (ADR-0007). Two implementations:
- `LocalStorageProvider` — writes to `upload_dir` (dev default).
- `S3StorageProvider` — writes to an S3-compatible bucket via `aioboto3`.

A `STORAGE_PROVIDER` setting (values `local` | `s3`) selects the implementation
at startup, resolved by a `get_storage_provider(settings)` factory. This mirrors
the existing `get_chat_provider(settings)` pattern.

### 2. SeaweedFS for self-hosted S3
SeaweedFS is chosen over MinIO and cloud S3:
- **MinIO** is production-grade but heavier — it requires a dedicated volume,
  has a complex console, and its S3 API only works after creating a bucket
  and access key through its web UI or CLI.
- **Cloud S3** (AWS, Backblaze, etc.) adds a cloud dependency and per-request
  cost, contradicting the self-hosted / low-cost MVP goal.
- **SeaweedFS** starts as a master + volume + filer with `-s3` flag from a
  single Docker image (`chrislusf/seaweedfs:latest`), no volume initialization
  or bucket creation steps. The S3 endpoint accepts any credentials, which
  simplifies local dev. Well-suited for the data volumes of a personal wiki.

The S3 provider configuration (endpoint, bucket, credentials) is designed to
work with any S3-compatible backend, so a future migration to MinIO or cloud
S3 requires only a settings change — no code changes.

### 3. Opaque storage key, not full URI
`upload()` returns an opaque key (e.g., `sources/{source_id}`) rather than a
full URI (e.g., `s3://bucket/sources/{source_id}`). Storing a URI would tempt
scheme-based routing at read time, which is unnecessary because only one
provider is active per deployment (single-tenant — ADR-0004). The source column
is renamed from `file_path` to `storage_key` to reflect the new semantics.

### 4. aioboto3 as the async S3 client
`aioboto3` wraps `aiobotocore` in the familiar boto3 resource/client API and
provides native async `upload_fileobj()` and `download_file()` — both critical
for our streaming upload from FastAPI and temp-file download in the worker.

`s3fs` was considered but rejected because its async path requires buffering
uploads to a temp file before `_put_file()`, defeating streaming.

### 5. Upload at API time, not worker time
The API upload handler writes the file through the StorageProvider immediately,
stores the resulting `storage_key` on the Source row, and enqueues the job with
the key. The worker reads from the provider at processing time. This keeps the
worker storage-agnostic and avoids leaving local temp files behind on failure.

## Rationale
- **Self-hosted, low-cost MVP**: SeaweedFS runs as a single container with no
  setup beyond the Docker Compose entry. No cloud costs, no external dependency.
- **Provider abstraction**: avoids coupling the codebase to any one storage
  backend. The pattern is already established and understood from ChatProvider.
- **Opaque key**: simpler than URI parsing, and routing logic would only be
  needed in a multi-backend scenario that doesn't exist.
- **aioboto3**: streaming upload from FastAPI `UploadFile` is the decisive
  feature — the entire file never needs to be in memory or on a temp path
  before reaching S3.
- **Upload at API time**: the API already has the file bytes in its request
  handler; forwarding them to storage immediately is zero extra I/O. The worker
  stays storage-agnostic.

## Consequences
- All file operations (upload, read, delete, exists check) now go through the
  `StorageProvider` interface. Direct filesystem access to source files is
  eliminated from worker and CLI code.
- The API route no longer writes to `upload_dir` directly — it delegates to the
  provider. The `upload_dir` setting is only relevant for `LocalStorageProvider`.
- The worker downloads source files to a temporary file before parsing — an
  additional I/O step relative to the current path-based read — but the
  CPU-bound parsing dominates wall time, making this negligible.
- The `sources.file_path` column is renamed to `sources.storage_key`, requiring
  an Alembic migration. Existing rows with absolute paths remain valid under
  `LocalStorageProvider` but will need migration or backfill logic if switching
  to S3 after data exists.
- `aioboto3` (and its `aiobotocore` dependency) is an optional dependency group
  (`pip install rag-wiki[s3]`), keeping the core install lean.
- The CLI `rag-wiki ingest <file_path>` now creates a Source row and uploads
  the file through the provider, then enqueues the job — mirroring the API
  flow rather than enqueuing a local path.
- Docker Compose dev setup includes SeaweedFS master/volume/filer services.
