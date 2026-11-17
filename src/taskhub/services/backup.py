"""Database backup helpers (M3 subset: SQLite online backup)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from taskhub.config import get_settings


def _sqlite_path(db_url: str) -> Path | None:
    """Extract the file path from a file-backed SQLite URL."""

    prefix = "sqlite+aiosqlite:///"
    if not db_url.startswith(prefix):
        return None
    rest = db_url[len(prefix) :]
    if rest in (":memory:", "") or rest.startswith("file::memory:"):
        return None
    return Path(rest)


async def backup_sqlite(db_url: str, backup_dir: Path) -> Path:
    """Create a consistent online SQLite backup with ``VACUUM INTO``."""

    db_path = _sqlite_path(db_url)
    if db_path is None:
        raise ValueError("backup is only implemented for file-backed SQLite URLs")
    if not db_path.exists():
        raise FileNotFoundError(f"database file not found: {db_path}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    target = backup_dir / f"taskhub-{stamp}.db"
    escaped = str(target).replace("'", "''")
    async with aiosqlite.connect(db_path) as connection:
        await connection.execute(f"VACUUM INTO '{escaped}'")
        await connection.commit()
    return target


async def create_backup(
    db_url: str | None = None,
    backup_dir: Path | None = None,
) -> Path:
    """Create a backup using settings defaults when arguments are omitted."""

    settings = get_settings()
    return await backup_sqlite(
        db_url or settings.db_url,
        backup_dir or settings.backup_dir,
    )
