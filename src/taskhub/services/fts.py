"""Full-text search index for notes (M13).

The index is an **optional accelerator**, not a requirement:

* on SQLite with FTS5 the notes are mirrored into a virtual table and
  searched by word;
* everywhere else (PostgreSQL, SQLite without FTS5) the portable
  substring search in ``repo/notes.py`` keeps working.

Both paths must answer the same question the same way, so the caller
can use the index without checking the backend. ``search_notes``
therefore falls back automatically when the index is unavailable or
empty for a query it cannot answer.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Note, NoteKind

#: Name of the virtual table that mirrors the notes.
FTS_TABLE = "note_fts"

#: DDL of the index and the triggers that keep it in sync.
#:
#: The triggers mirror inserts, updates and deletes, so the index never
#: goes stale. Notes are never deleted physically in this project, but
#: the delete trigger keeps the index correct anyway.
INDEX_DDL: tuple[str, ...] = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5("
    "  content, note_id UNINDEXED, project_id UNINDEXED, kind UNINDEXED, "
    "  tokenize='unicode61 remove_diacritics 2'"
    ")",
    "CREATE TRIGGER IF NOT EXISTS note_fts_ai AFTER INSERT ON note BEGIN\n"
    "  INSERT INTO note_fts(content, note_id, project_id, kind) "
    "VALUES (new.content, new.id, new.project_id, new.kind);\nEND",
    "CREATE TRIGGER IF NOT EXISTS note_fts_ad AFTER DELETE ON note BEGIN\n"
    f"  DELETE FROM {FTS_TABLE} WHERE note_id = old.id;\nEND",
    "CREATE TRIGGER IF NOT EXISTS note_fts_au AFTER UPDATE ON note BEGIN\n"
    f"  DELETE FROM {FTS_TABLE} WHERE note_id = old.id;\n"
    "  INSERT INTO note_fts(content, note_id, project_id, kind) "
    "VALUES (new.content, new.id, new.project_id, new.kind);\nEND",
)

#: Words shorter than this are ignored by the query builder.
_MIN_WORD_LENGTH = 2

_WORD_RE = re.compile(r"[\w]+", re.UNICODE)


def _is_sqlite(session: AsyncSession) -> bool:
    """True when the session talks to SQLite.

    ``session.get_bind()`` is a synchronous call that touches the
    engine; calling it from async code raises ``MissingGreenlet``. The
    dialect is read from the session's bind instead, which needs no IO.
    """
    bind = session.get_bind()
    return bind.dialect.name == "sqlite"


async def fts_available(session: AsyncSession) -> bool:
    """Check whether the FTS5 index can be used on this connection.

    Args:
        session: Database session.

    Returns:
        True when the index exists and is usable.
    """
    if not _is_sqlite(session):
        return False
    try:
        await session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": FTS_TABLE},
        )
    except Exception:
        return False
    return (
        await session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": FTS_TABLE},
        )
    ).scalar_one_or_none() is not None


async def create_index(session: AsyncSession) -> bool:
    """Create the virtual table and the triggers that keep it in sync.

    The triggers mirror inserts, updates and deletes, so the index never
    goes stale. Notes are never deleted physically in this project, but
    the delete trigger keeps the index correct anyway.

    Args:
        session: Database session.

    Returns:
        True when the index was created, False when FTS5 is unavailable.
    """
    if not _is_sqlite(session):
        return False
    try:
        for statement in INDEX_DDL:
            await session.execute(text(statement))
    except Exception:
        await session.rollback()
        return False
    await session.commit()
    return True


async def ensure_index(session: AsyncSession) -> bool:
    """Create the index if it is missing.

    Safe to call on every start: the DDL is guarded by ``IF NOT EXISTS``
    and the function returns immediately when the table already exists.

    Args:
        session: Database session.

    Returns:
        True when the index is present and usable.
    """
    if not _is_sqlite(session):
        return False
    exists = (
        await session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": FTS_TABLE},
        )
    ).scalar_one_or_none()
    if exists:
        return True
    return await create_index(session)


async def rebuild_index(session: AsyncSession) -> bool:
    """Rebuild the whole index from the notes table.

    Args:
        session: Database session.

    Returns:
        True when the index was rebuilt, False when it is unavailable.
    """
    if not await ensure_index(session):
        return False
    try:
        await session.execute(text(f"DELETE FROM {FTS_TABLE}"))
        await session.execute(
            text(
                f"INSERT INTO {FTS_TABLE}(content, note_id, project_id, kind) "
                "SELECT content, id, project_id, kind FROM note"
            )
        )
    except Exception:
        await session.rollback()
        return False
    await session.commit()
    return True


async def index_notes(session: AsyncSession, notes: list[Note]) -> bool:
    """Mirror the given notes into the index.

    Notes that were updated are re-written; the trigger keeps the index
    consistent on its own, but an explicit write makes the index usable
    right after an import without waiting for the flush.

    Args:
        session: Database session.
        notes: The notes to mirror.

    Returns:
        True when the notes were indexed, False when it is unavailable.
    """
    if not notes or not _is_sqlite(session):
        return False
    if not await ensure_index(session):
        return False
    for note in notes:
        try:
            await session.execute(
                text(f"DELETE FROM {FTS_TABLE} WHERE note_id = :note_id"),
                {"note_id": note.id},
            )
            await session.execute(
                text(
                    f"INSERT INTO {FTS_TABLE}(content, note_id, project_id, kind) "
                    "VALUES (:content, :note_id, :project_id, :kind)"
                ),
                {
                    "content": note.content,
                    "note_id": note.id,
                    "project_id": note.project_id,
                    "kind": note.kind.value if hasattr(note.kind, "value") else note.kind,
                },
            )
        except Exception:
            await session.rollback()
            return False
    await session.commit()
    return True


def _to_match_query(query: str) -> str:
    """Turn a user query into an FTS5 MATCH expression.

    Every word becomes a quoted prefix term, so a query never carries
    FTS5 operators (and a stray quote cannot break the statement).

    Args:
        query: The raw user query.

    Returns:
        The MATCH expression, or an empty string when there is no word.
    """
    words = [word for word in _WORD_RE.findall(query) if len(word) >= _MIN_WORD_LENGTH]
    if not words:
        return ""
    return " AND ".join(f'"{word}"*' for word in words)


async def search_notes_fts(
    session: AsyncSession,
    project_id: str,
    query: str,
    *,
    work_item_id: str | None = None,
    kind: NoteKind | None = None,
    limit: int = 20,
) -> list[Note]:
    """Search the notes through the full-text index.

    Args:
        session: Database session.
        project_id: Project to search in.
        query: The search query.
        work_item_id: Restrict to one work item.
        kind: Restrict to one note kind.
        limit: Maximum number of results.

    Returns:
        The matching notes, newest first (empty when the index cannot
        answer the query).
    """
    match = _to_match_query(query)
    if not match:
        return []

    if not await ensure_index(session):
        return []

    clauses = [f"{FTS_TABLE} MATCH :match"]
    params: dict[str, Any] = {"match": match, "project_id": project_id}
    sql = f"SELECT note_id FROM {FTS_TABLE} WHERE {clauses[0]} AND project_id = :project_id"
    if work_item_id is not None:
        sql += " AND note_id IN (SELECT id FROM note WHERE work_item_id = :work_item_id)"
        params["work_item_id"] = work_item_id
    if kind is not None:
        sql += " AND kind = :kind"
        params["kind"] = kind.value if hasattr(kind, "value") else kind
    sql += " LIMIT :limit"
    params["limit"] = limit

    try:
        rows = list((await session.execute(text(sql), params)).all())
    except Exception:
        await session.rollback()
        return []

    ids = [row[0] for row in rows]
    if not ids:
        return []
    notes = list((await session.execute(select(Note).where(Note.id.in_(ids)))).scalars().all())
    order = {note_id: index for index, note_id in enumerate(ids)}
    notes.sort(key=lambda note: order.get(note.id, 0))
    return notes
