from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql+asyncpg://dduo_solo_founder:dduo_solo_founder@postgres:5432/dduo_solo_founder"
    )
    qdrant_url: str = "http://qdrant:6333"
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-large"
    embedding_index_version: str = "v2"
    # Tasks are a rebuildable projection with an independent render/index
    # lifecycle from consolidated memories.
    task_embedding_index_version: str = "v1"
    task_index_max_characters: int = Field(default=16_000, ge=512, le=100_000)
    # A byte ceiling is model-independent and conservative: a tokenizer cannot
    # emit more tokens than the number of non-empty UTF-8 byte spans it consumes.
    # Keeping the document below 7,500 bytes leaves headroom under OpenAI's
    # 8,191-token embedding input limit even for dense CJK or emoji text.
    task_index_max_utf8_bytes: int = Field(default=7_500, ge=512, le=8_000)
    task_retrieval_similarity_threshold: float = Field(default=0.35, ge=-1.0, le=1.0)
    openai_api_key: str | None = None
    dduo_solo_founder_api_url: str = "http://127.0.0.1:8765"
    collection_prefix: str = "dduo_solo_founder"
    # The launcher injects the agent's dynamic URL. Port zero is a deliberate
    # unusable sentinel for unsupported direct Compose runs.
    cli_bridge_url: str = "http://host.docker.internal:0"
    cli_bridge_token: str = ""
    # One compact consolidation batch keeps subscription-CLI work predictable.
    sleep_idle_seconds: int = 20 * 60
    sleep_turn_threshold: int = 8
    sleep_batch_size: int = 8
    sleep_batch_char_limit: int = 40_000
    sleep_poll_seconds: float = 2.0
    sleep_cli_timeout_seconds: int = 900
    retrieval_similarity_threshold: float = 0.5
    retrieval_episode_limit: int = 3
    retrieval_fact_limit: int = 4
    retrieval_heuristic_limit: int = 4
    artifact_max_bytes: int = 10 * 1024 * 1024
    backup_configured: bool = False
    backup_directory: Path = Path("/backups")
    backup_key_file: Path = Path("/run/secrets/dduo-backup-key")
    project_config_file: Path = Path("/project-config/project.toml")
    backup_include_qdrant: bool = True
    backup_retention_daily: int = 7
    backup_retention_weekly: int = 4
    backup_retention_monthly: int = 6
    backup_auto_seconds: int = 5 * 60
    backup_configuration_error: str = ""
    backup_project_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
