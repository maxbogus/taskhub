"""Async database engine, session factory and schema bootstrap helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from taskhub.config import get_settings
from taskhub.models import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def sqlite_file_path(db_url: str) -> Path | None:
    """Return the filesystem path of a file-backed SQLite URL, else ``None``."""

    prefix = "sqlite+aiosqlite:///"
    if not db_url.startswith(prefix):
        return None
    rest = db_url[len(prefix) :]
    if rest in ("", ":memory:") or rest.startswith("file::memory:"):
        return None
    return Path(rest)


def ensure_db_parent(db_url: str) -> None:
    """Create the parent directory of a file-backed SQLite database."""

    path = sqlite_file_path(db_url)
    if path is not None:
        path.expanduser().parent.mkdir(parents=True, exist_ok=True)


def get_engine(db_url: str | None = None) -> AsyncEngine:
    """Return the process-wide async engine, creating it on first use."""

    global _engine
    if _engine is None:
        url = db_url or get_settings().db_url
        ensure_db_parent(url)
        _engine = create_async_engine(url, echo=False, future=True)
    return _engine


def get_sessionmaker(db_url: str | None = None) -> async_sessionmaker[AsyncSession]:
    """Return the process-wide session factory."""

    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(db_url), expire_on_commit=False)
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Open a session, roll back on error and always close it."""

    async with get_sessionmaker()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_db(db_url: str | None = None) -> None:
    """Create all tables (used by tests and the early CLI).

    The full-text index for notes is created as well: it is optional, so
    a backend without FTS5 simply does without it.
    """

    engine = get_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_note_index()


async def ensure_note_index() -> bool:
    """Create the note full-text index when the backend supports it.

    Returns:
        True when the index exists after the call.
    """
    from taskhub.services.fts import ensure_index

    async with session_scope() as session:
        return await ensure_index(session)


async def dispose_engine() -> None:
    """Dispose the engine and reset the global state (tests, shutdown)."""

    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
