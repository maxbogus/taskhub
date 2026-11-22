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
    """Search the notes of a project.

    The search combines two complementary strategies:

    * the full-text index (FTS5 on SQLite) finds whole words and their
      prefixes — it is what makes a search fast on a large note table;
    * the portable substring search finds occurrences **inside** a word
      ("gres" in "Postgres"), which the index cannot match.

    Both are used, so the result set stays the same as the plain substring
    search used to be — the index adds speed, never different answers.
    """

    if not query.strip():
        return []

    from taskhub.services.fts import search_notes_fts

    stmt = select(Note).where(
        Note.project_id == project_id,
        Note.content.contains(query, autoescape=True),
    )
    if work_item_id is not None:
        stmt = stmt.where(Note.work_item_id == work_item_id)
    if kind is not None:
        stmt = stmt.where(Note.kind == kind)
    stmt = stmt.order_by(Note.created_at.desc()).limit(limit)
    plain = list((await session.execute(stmt)).scalars().all())

    indexed = await search_notes_fts(
        session,
        project_id,
        query,
        work_item_id=work_item_id,
        kind=kind,
        limit=limit,
    )
    if not indexed:
        return plain

    # Merge both sets, keeping the order of the substring search and
    # adding the index-only hits at the end.
    merged: list[Note] = list(plain)
    known = {note.id for note in plain}
    merged.extend(note for note in indexed if note.id not in known)
    return merged[:limit]
