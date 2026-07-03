from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Hostnames treated as loopback for the MCP HTTP transport hardening
# (ADR-0017 §6). Binds to anything outside this set are rejected when
# `mcp_transport == "http"` so an unauthenticated MCP HTTP endpoint can
# never be exposed to the network.
MCP_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


class Settings(BaseSettings):
    """
    Application-wide configuration loaded from environment variables.

    All runtime configuration (database, LLM, embedding, planner, retrieval,
    worker, API, logging) is sourced from env vars via pydantic-settings.
    Never hardcode config values in application code.
    """

    # Database
    database_url: str

    # LLM provider
    llm_provider: Literal["openai", "anthropic"] = "openai"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_api_version: str | None = None

    # Per-operation models
    llm_model_caption: str = "gpt-4o-mini"
    llm_model_extraction: str = "gpt-4o-mini"
    llm_model_resolution: str = "gpt-4o"
    llm_model_wiki_synthesis: str = "gpt-4o"
    llm_model_query: str = "gpt-4o"

    # Embeddings
    llm_embedding_provider: str = "openai"
    embedding_model: str = "gemini-embedding-2"
    embedding_dimensions: int = 3072
    send_dimensions: bool = True
    gemini_api_key: str | None = None
    embedding_task_type: str = "RETRIEVAL_DOCUMENT"

    # Entity resolution
    entity_resolution_top_k: int = 5
    entity_resolution_distance_threshold: float = 0.6

    # pgvector HNSW index settings
    hnsw_m: int = 24
    hnsw_ef_construction: int = 200

    # Planner
    planner_version: str = "1.0.0"
    planner_confidence_high: float = 0.8
    planner_confidence_low: float = 0.5
    planner_confidence_minimum: float = 0.5
    planner_density_large_threshold_bytes: int = 10_485_760
    llm_model_query_classification: str = "gpt-4o-mini"
    planner_query_classification_timeout_ms: int = 500

    # Retrieval
    retrieval_seed_count: int = 3
    retrieval_max_hops: int = 2
    retrieval_max_neighbors_per_hop: int = 10
    retrieval_max_total_nodes: int = 50
    retrieval_dedup_threshold: float = 0.92
    retrieval_total_budget_tokens: int = 3600
    retrieval_anchor_budget_tokens: int = 200
    retrieval_subgraph_budget_tokens: int = 400
    retrieval_wiki_page_budget_tokens: int = 1000
    retrieval_instruction_budget_tokens: int = 200

    # Worker
    worker_poll_interval_seconds: int = 2
    worker_max_retries: int = 3

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    upload_dir: Path = Path("./uploads")
    upload_max_file_size_bytes: int = 104_857_600
    cors_origins: str = ""

    # Storage provider
    storage_provider: Literal["local", "s3"] = "local"
    s3_bucket: str = "rag-wiki"
    s3_endpoint_url: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "us-east-1"

    # MCP server
    mcp_transport: Literal["stdio", "http"] = "stdio"
    mcp_api_url: AnyHttpUrl = "http://127.0.0.1:8000"  # type: ignore[assignment]
    mcp_host: str = "127.0.0.1"
    mcp_port: int | None = None

    # Logging
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "console"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    @model_validator(mode="after")
    def _validate_mcp_http_loopback(self) -> "Settings":
        """
        Harden the MCP HTTP transport to loopback binds only.

        ADR-0017 §6 / PRD-002 user story 13: when the MCP server runs over
        HTTP it must bind to a loopback address so an unauthenticated MCP
        endpoint is never reachable over the tailnet. stdio is a local-trust
        transport and imposes no host constraint.

        Raises:
            ValueError: If ``mcp_transport == "http"`` and ``mcp_host`` is not
                a loopback address (``127.0.0.1``, ``::1``, ``localhost``).
        """
        if self.mcp_transport == "http" and self.mcp_host not in MCP_LOOPBACK_HOSTS:
            raise ValueError(
                "MCP HTTP transport must bind to a loopback address "
                f"(one of {sorted(MCP_LOOPBACK_HOSTS)}); got mcp_host="
                f"{self.mcp_host!r}. Set MCP_TRANSPORT=stdio, or bind MCP_HOST "
                "to 127.0.0.1 / ::1 / localhost. (ADR-0017 §6: unauthenticated "
                "MCP HTTP must not be exposed to the network.)"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """
    Return a cached Settings instance.

    Uses lru_cache so Settings() is constructed exactly once per process.
    All callers should use this function rather than instantiating Settings
    directly to avoid repeated env-var reads.

    Returns:
        A singleton Settings object populated from environment variables.
    """
    return Settings()
