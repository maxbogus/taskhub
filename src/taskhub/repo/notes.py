"""Note repository: append-only notes on projects and work items."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Note, NoteKind


async def add_note(
    session: AsyncSession,
    *,
    project_id: str,
    content: str,
    work_item_id: str | None = None,
    kind: NoteKind = NoteKind.comment,
    author: str | None = None,
    external_ref: str | None = None,
    meta: dict[str, Any] | None = None,
) -> Note:
    """Insert a note and flush it so its generated id is available."""

    note = Note(
        project_id=project_id,
        work_item_id=work_item_id,
        kind=kind,
        content=content,
        author=author,
        external_ref=external_ref,
        meta=meta or {},
    )
    session.add(note)
    await session.flush()
    return note


async def list_notes(
    session: AsyncSession,
    project_id: str,
    *,
    work_item_id: str | None = None,
    kind: NoteKind | None = None,
    limit: int = 50,
) -> list[Note]:
    """Return the most recent notes for a project (optionally filtered)."""

    stmt = select(Note).where(Note.project_id == project_id)
    if work_item_id is not None:
        stmt = stmt.where(Note.work_item_id == work_item_id)
    if kind is not None:
        stmt = stmt.where(Note.kind == kind)
    stmt = stmt.order_by(Note.created_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def search_notes(
    session: AsyncSession,
    project_id: str,
    query: str,
    *,
    work_item_id: str | None = None,
    kind: NoteKind | None = None,
    limit: int = 20,
) -> list[Note]:
    """Substring search over note content (portable across SQLite/PostgreSQL)."""

    if not query.strip():
        return []
    stmt = select(Note).where(
        Note.project_id == project_id,
        Note.content.contains(query, autoescape=True),
    )
    if work_item_id is not None:
        stmt = stmt.where(Note.work_item_id == work_item_id)
    if kind is not None:
        stmt = stmt.where(Note.kind == kind)
    stmt = stmt.order_by(Note.created_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())
