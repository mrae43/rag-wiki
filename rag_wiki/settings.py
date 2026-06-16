from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
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

    # Entity resolution
    entity_resolution_top_k: int = 5
    entity_resolution_distance_threshold: float = 0.6

    # pgvector HNSW index settings
    hnsw_m: int = 24
    hnsw_ef_construction: int = 200

    # Parser
    parser: Literal["lightweight", "mineru"] = "lightweight"

    # Worker
    worker_poll_interval_seconds: int = 2
    worker_max_retries: int = 3

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Logging
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "console"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
