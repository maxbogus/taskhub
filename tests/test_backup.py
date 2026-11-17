"""Tests for SQLite online backup."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from taskhub.services.backup import backup_sqlite, prune_backups


async def test_backup_sqlite_copies_data(tmp_path: Path) -> None:
    db_path = tmp_path / "data.db"
    async with aiosqlite.connect(db_path) as connection:
        await connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        await connection.execute("INSERT INTO t (name) VALUES ('x')")
        await connection.commit()

    target = await backup_sqlite(
        f"sqlite+aiosqlite:///{db_path}", tmp_path / "backups"
    )

    assert target.exists()
    with sqlite3.connect(target) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        assert ("t",) in tables
        assert connection.execute("SELECT name FROM t").fetchone() == ("x",)


async def test_backup_rejects_in_memory_database() -> None:
    with pytest.raises(ValueError, match="file-backed"):
        await backup_sqlite("sqlite+aiosqlite:///:memory:", Path("/tmp"))


async def test_backup_retention_keeps_newest(tmp_path: Path) -> None:
    db_path = tmp_path / "data.db"
    async with aiosqlite.connect(db_path) as connection:
        await connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        await connection.commit()

    backup_dir = tmp_path / "backups"
    for _ in range(3):
        await backup_sqlite(f"sqlite+aiosqlite:///{db_path}", backup_dir, keep=2)

    files = sorted(backup_dir.glob("taskhub-*.db"))
    assert len(files) == 2


async def test_prune_backups_non_positive_keeps_all(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (backup_dir / "taskhub-20260101-000000-000001.db").write_bytes(b"")
    (backup_dir / "taskhub-20260101-000000-000002.db").write_bytes(b"")

    removed = prune_backups(backup_dir, 0)

    assert removed == []
    assert len(list(backup_dir.glob("taskhub-*.db"))) == 2
