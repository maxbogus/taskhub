"""Application settings loaded from TASKHUB_* environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for TaskHub."""

    model_config = SettingsConfigDict(
        env_prefix="TASKHUB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_url: str = "sqlite+aiosqlite:///./taskhub.db"
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 7801
    token: str | None = None
    backup_dir: Path = Path("./backups")
    metrics_sources: str = "cline"
    log_level: str = "INFO"
    default_claim_ttl_minutes: int = 30


def get_settings() -> Settings:
    """Return a fresh settings instance (env is read on each call)."""

    return Settings()
