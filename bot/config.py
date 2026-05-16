"""Application settings. Secrets wrapped in SecretStr to prevent log/repr leaks."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: SecretStr
    openai_api_key: SecretStr
    api_admin_token: SecretStr

    admin_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    admin_chat_id: int = 0

    database_url: str = "sqlite+aiosqlite:///data/bot.db"

    openai_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    openai_timeout_seconds: float = 30.0
    openai_max_retries: int = 6

    relevance_threshold: float = 0.75
    relevance_min_gap: float = 0.05
    rerank_enabled: bool = False
    rerank_top_n: int = 5
    bm25_enabled: bool = True
    top_k_dense: int = 50
    top_k_bm25: int = 50
    top_k_final: int = 10
    rrf_k: int = 60

    max_chunk_tokens: int = 500
    chunk_overlap: int = 100
    max_upload_bytes: int = 10 * 1024 * 1024
    max_chunks_per_doc: int = 5000

    api_port: int = 8000
    metrics_internal_only: bool = False

    log_level: str = "INFO"
    log_json: bool = True
    sentry_dsn: SecretStr | None = None
    environment: str = "production"

    throttle_per_user_per_sec: float = 1.0

    seed_sample_on_first_run: bool = True

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, v: object) -> list[int]:
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, list | tuple):
            return [int(x) for x in v]
        raise ValueError(f"admin_ids: unsupported type {type(v)}")

    @field_validator("relevance_threshold", "relevance_min_gap")
    @classmethod
    def _check_unit_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("value must be in [0, 1]")
        return v

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return upper


settings = Settings()  # type: ignore[call-arg]
