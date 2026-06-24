# PRD: S3/SeaweedFS Storage Provider

## Problem Statement

Raw source documents (PDFs, images, articles) are currently stored on the local
filesystem via `upload_dir`. This breaks in any multi-process deployment: the
API container writes a file that the worker container cannot read unless both
share a bind mount. Horizontal scaling (multiple API or worker replicas) is
impossible. A self-hosted S3-compatible store solves the shared-storage problem
while keeping the deployment self-contained and low-cost.

## Solution

Introduce a `StorageProvider` protocol (matching the existing ADR-0007 provider
pattern) with two implementations: `LocalStorageProvider` for development and
`S3StorageProvider` backed by SeaweedFS for production/self-hosted deployments.
All file operations (upload, download, delete, exists) go through the provider.
The API uploads files at request time, the worker downloads to a temp file
before parsing.

## User Stories

1. As a developer, I want to run the system with a single `docker compose up`
   and have local filesystem storage work out of the box, so that I can develop
   without setting up S3 infrastructure.

2. As a developer, I want to switch between local and S3 storage by changing one
   environment variable, so that I can test both paths without code changes.

3. As a self-hosted operator, I want to deploy the full stack (API + worker +
   database + storage) with a single `docker compose up`, so that I don't need
   to configure external infrastructure.

4. As a self-hosted operator, I want the storage layer to use SeaweedFS, so that
   I get an S3-compatible API without cloud costs or complex setup.

5. As a user, I want to upload a document via the API and have it immediately
   available for the worker to process, even when API and worker run in
   different containers, so that the system works reliably in containerized
   deployments.

6. As a user, I want to delete a source and have its raw file deleted from
   storage automatically, so that storage is cleaned up without manual
   intervention.

7. As a developer, I want to ingest a document from the CLI without needing the
   API server running, so that I can test ingestion locally.

8. As an operator, I want to swap SeaweedFS for MinIO or cloud S3 by changing
   only environment variables and not code, so that I can migrate storage
   backends without a deployment cycle.

9. As a developer, I want the storage provider to expose a `with_temp_file(key)`
   context manager that handles download, temp file lifecycle, and cleanup, so
   that the ingestion pipeline doesn't repeat temp-file boilerplate.

10. As a developer, I want all storage operations to raise `StorageError` on
    failure, so that error handling is consistent across providers.

11. As a developer, I want the `S3StorageProvider` to be an optional dependency
    (`pip install rag-wiki[s3]`), so that the core install stays lean.

12. As a developer, I want unit tests for both providers that verify the
    protocol contract (upload → download → delete roundtrip), so that I can
    validate storage behaviour without running the full pipeline.

## Implementation Decisions

### Provider protocol

A `StorageProvider` protocol in `storage/__init__.py` with four async methods:

- `upload(source_id: str, file: BinaryIO, filename: str) -> str` — stores the
  file bytes, returns a storage key in the format `sources/{source_id}`.
- `download(key: str) -> AsyncIterator[bytes]` — yields the stored file in
  chunks.
- `delete(key: str) -> None` — removes the stored object.
- `exists(key: str) -> bool` — returns whether the object exists.

Plus a context manager helper:

- `with_temp_file(key: str) -> AsyncIterator[Path]` — downloads to a temp
  file, yields the path, and deletes the temp file on exit.

### Provider registry

A dict-based registry and `get_storage_provider(settings)` factory, mirroring
the existing `get_chat_provider(settings)` in `providers/__init__.py`.

### LocalStorageProvider

Writes to `{upload_dir}/sources/{source_id}` using `aiofiles`. Reads from the
same path. Uses `os.path.isfile` for existence checks. This preserves backward
compatibility for existing local deployments.

### S3StorageProvider

Wraps `aioboto3` to interact with any S3-compatible API. Configuration via
env vars: `S3_BUCKET`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`,
`S3_SECRET_ACCESS_KEY`, `S3_REGION`. All are optional — defaults work with
local SeaweedFS dev setup.

### SeaweedFS in Docker Compose

Three services: `seaweedfs_master`, `seaweedfs_volume`, `seaweedfs_filer` (with
`-s3` flag on port 8333). No volume initialization or bucket creation needed.

### Schema change

`sources.file_path` renamed to `sources.storage_key` via Alembic autogenerate
migration. Existing rows with absolute paths remain valid under
`LocalStorageProvider`.

## Testing Decisions

- **What makes a good test**: test the external contract, not the implementation
  detail. For storage providers: upload a file → verify exists → download it →
  verify content matches → delete it → verify not exists. This roundtrip applies
  to both providers.

- **Test modules**:
  - `tests/storage/test_storage_local.py` — `LocalStorageProvider` against a
    `tempfile.TemporaryDirectory`
  - `tests/storage/test_storage_s3.py` — `S3StorageProvider` against a real
    S3 endpoint (minio or SeaweedFS in CI), `pytest.mark.skipif` when unavailable
  - `tests/storage/test_storage_smoke.py` — protocol contract tests that run
    against any provider via a fixture, ensuring both implementations satisfy
    the same contract.
  - `tests/conftest.py` — add `FakeStorageProvider` fixture returning in-memory
    storage for downstream tests
  - `tests/api/test_source.py` — update upload test to verify `storage_key`
    instead of `file_path` in the response
  - `tests/ingest/test_pipeline.py` — verify the temp-file download step
    (mock storage returns bytes, verify pipeline calls parse with a real file)

- **Prior art**: the existing `FakeChatProvider` and `FakeEmbeddingProvider` in
  `tests/conftest.py` establish the pattern for provider test doubles. The
  `FakeStorageProvider` will follow the same pattern — a dict-based in-memory
  store.

## Out of Scope

- Migration tooling for existing uploaded files when switching from local to S3
  storage. This can be addressed when an operator actually needs it.
- File versioning or retention policies in S3. The system stores the current
  version only; deletion is the only lifecycle operation.
- Multi-bucket support. A single bucket is sufficient for a single-tenant
  deployment (ADR-0004).
- Cloud S3-specific features (bucket policies, IAM roles, S3 Transfer
  Acceleration). The provider can be extended later.
- Obsidian export file storage. Wiki page exports are generated on demand and
  written locally; they are not source documents.

## Further Notes

This PRD was produced from an interview-driven planning session that explored
the codebase, resolved dependencies between design decisions one-by-one, and
produced ADR-0015 documenting the architectural rationale. The implementation
order defined in ADR-0015 should be followed: protocol first, then local
provider, then S3 provider, then wiring through the existing code paths, then
Docker Compose changes.
