"""Application settings loaded from TASKHUB_* environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_DATA_DIR = Path.home() / ".taskhub"
_DEFAULT_DB_URL = f"sqlite+aiosqlite:///{_DEFAULT_DATA_DIR / 'taskhub.db'}"


class Settings(BaseSettings):
    """Runtime configuration for TaskHub."""

    model_config = SettingsConfigDict(
        env_prefix="TASKHUB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_url: str = _DEFAULT_DB_URL
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 7801
    web_port: int = 8000
    token: str | None = None
    backup_dir: Path = _DEFAULT_DATA_DIR / "backups"
    backup_keep: int = 7
    repositories_root: Path = Path.home() / "Repositories"
    sources_file: Path | None = None
    memory_root: Path = Path.home() / ".cline" / "memory"
    metrics_sources: str = "cline"
    log_level: str = "INFO"
    default_claim_ttl_minutes: int = 30


def get_settings() -> Settings:
    """Return a fresh settings instance (env is read on each call)."""

    return Settings()
