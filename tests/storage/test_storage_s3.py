"""
tests.storage.test_storage_s3
-----------------------------
Contract tests for S3StorageProvider against a live S3-compatible endpoint.
Skipped automatically when rag-wiki[s3] extra or RAG_WIKI_TEST_S3_ENDPOINT
is absent.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

import pytest

from rag_wiki.settings import Settings

try:
    from rag_wiki.storage.s3 import S3StorageProvider
except ImportError:
    S3StorageProvider = None  # type: ignore[assignment,misc]

pytestmark = pytest.mark.skipif(
    S3StorageProvider is None or not os.getenv("RAG_WIKI_TEST_S3_ENDPOINT"),
    reason="rag-wiki[s3] extra not installed or RAG_WIKI_TEST_S3_ENDPOINT not set",
)


@pytest.fixture
def provider() -> S3StorageProvider:
    settings = Settings(
        database_url="postgresql+asyncpg://u:p@localhost:5432/db",
        s3_endpoint_url=os.environ["RAG_WIKI_TEST_S3_ENDPOINT"],
        s3_access_key_id=os.getenv("RAG_WIKI_TEST_S3_ACCESS_KEY_ID", "test"),
        s3_secret_access_key=os.getenv("RAG_WIKI_TEST_S3_SECRET_ACCESS_KEY", "test"),
        s3_bucket=os.getenv("RAG_WIKI_TEST_S3_BUCKET", "rag-wiki-test"),
        s3_region=os.getenv("RAG_WIKI_TEST_S3_REGION", "us-east-1"),
    )
    return S3StorageProvider(settings)


from tests.storage.test_storage_smoke import CONTRACT_TESTS  # noqa: E402


@pytest.mark.parametrize("contract_test", CONTRACT_TESTS)
async def test_contract(
    contract_test: Callable[..., Awaitable[None]],
    provider: S3StorageProvider,
) -> None:
    await contract_test(provider)
