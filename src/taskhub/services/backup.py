"""Database backup helpers (M3 subset: SQLite online backup)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from taskhub.config import get_settings
from taskhub.db import sqlite_file_path


def _backup_files(backup_dir: Path) -> list[Path]:
    """Return backup files newest-first (timestamped names sort chronologically)."""

    return sorted(
        backup_dir.glob("taskhub-*.db"), key=lambda path: path.name, reverse=True
    )


def prune_backups(backup_dir: Path, keep: int) -> list[Path]:
    """Delete all but the newest ``keep`` backups; return removed paths.

    A non-positive ``keep`` disables pruning (keeps everything).
    """

    if keep <= 0:
        return []
    removed: list[Path] = []
    for path in _backup_files(backup_dir)[keep:]:
        path.unlink(missing_ok=True)
        removed.append(path)
    return removed


async def backup_sqlite(
    db_url: str, backup_dir: Path, *, keep: int | None = None
) -> Path:
    """Create a consistent online SQLite backup with ``VACUUM INTO``."""

    db_path = sqlite_file_path(db_url)
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
    if keep is not None:
        prune_backups(backup_dir, keep)
    return target


async def create_backup(
    db_url: str | None = None,
    backup_dir: Path | None = None,
    *,
    keep: int | None = None,
) -> Path:
    """Create a backup, applying retention when ``keep`` is configured."""

    settings = get_settings()
    effective_keep = settings.backup_keep if keep is None else keep
    return await backup_sqlite(
        db_url or settings.db_url,
        backup_dir or settings.backup_dir,
        keep=effective_keep,
    )
