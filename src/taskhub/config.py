"""Application settings loaded from TASKHUB_* environment variables.

No machine-specific absolute paths are baked in: the database and backup
locations are derived from ``TASKHUB_HOME`` (default ``~/.taskhub``) unless they
are overridden explicitly. Every setting can be changed with the matching
``TASKHUB_*`` environment variable or a local ``.env`` file.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_HOME = Path.home() / ".taskhub"
DEFAULT_DB_URL = f"sqlite+aiosqlite:///{DEFAULT_HOME / 'taskhub.db'}"


class Settings(BaseSettings):
    """Runtime configuration for TaskHub."""

    model_config = SettingsConfigDict(
        env_prefix="TASKHUB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    home: Path = DEFAULT_HOME
    db_url: str = DEFAULT_DB_URL
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 7801
    web_port: int = 8000
    token: str | None = None
    backup_dir: Path = DEFAULT_HOME / "backups"
    backup_keep: int = 7
    repositories_root: Path = Path.home() / "Repositories"
    sources_file: Path | None = None
    memory_root: Path = Path.home() / ".cline" / "memory"
    project_dir: Path | None = None
    metrics_sources: str = "cline"
    log_level: str = "INFO"
    default_claim_ttl_minutes: int = 30
    prices_file: Path | None = None
    human_rate_usd_per_hour: float = 50.0
    error_cost_usd: float = 0.0
    supervision_cost_usd: float = 0.0

    @model_validator(mode="before")
    @classmethod
    def _derive_home_paths(cls, values: Any) -> Any:
        """Point the database and backups at ``TASKHUB_HOME`` unless overridden."""

        if not isinstance(values, Mapping):
            return values
        data = dict(values)
        home = Path(str(data.get("home") or DEFAULT_HOME)).expanduser()
        data["home"] = home
        if not data.get("db_url"):
            data["db_url"] = f"sqlite+aiosqlite:///{home / 'taskhub.db'}"
        if not data.get("backup_dir"):
            data["backup_dir"] = home / "backups"
        return data


def get_settings() -> Settings:
    """Return a fresh settings instance (env is read on each call)."""

    return Settings()
