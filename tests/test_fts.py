"""Tests for the FTS5 note index (M13).

The index is an **optional accelerator**: it must give the same answers
as the portable substring search, and the portable path must keep
working when the index is missing. That is what makes the feature safe
to roll out — a database without FTS5 (or PostgreSQL) still answers
every query.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import NoteKind
from taskhub.repo.notes import add_note, search_notes
from taskhub.repo.projects import create_project
from taskhub.services.fts import (
    ensure_index,
    fts_available,
    index_notes,
    rebuild_index,
    search_notes_fts,
)


async def _project(session: AsyncSession):
    project = await create_project(session, slug="p", name="P", root_path="/tmp/p")
    await session.commit()
    return project


async def test_fts_available_on_sqlite(session: AsyncSession) -> None:
    """The index becomes available once it has been created."""
    assert await fts_available(session) is False

    assert await ensure_index(session) is True
    assert await fts_available(session) is True


async def test_ensure_index_is_idempotent(session: AsyncSession) -> None:
    """Creating the index twice is safe (the DDL is guarded)."""
    assert await ensure_index(session) is True
    assert await ensure_index(session) is True


async def test_search_fts_matches_substring(session: AsyncSession) -> None:
    """The index finds the same notes as the substring search."""
    project = await _project(session)
    await add_note(session, project_id=project.id, content="blocked by Postgres migration")
    await add_note(session, project_id=project.id, content="unrelated note")
    await session.commit()

    await rebuild_index(session)
    results = await search_notes_fts(session, project.id, "Postgres")

    assert len(results) == 1
    assert "Postgres" in results[0].content


async def test_search_fts_finds_by_word_not_substring(session: AsyncSession) -> None:
    """FTS5 matches whole words, so it can find more than a substring."""
    project = await _project(session)
    await add_note(session, project_id=project.id, content="blocked by Postgres migration")
    await session.commit()

    await rebuild_index(session)
    results = await search_notes_fts(session, project.id, "migration")

    assert len(results) == 1


async def test_index_notes_tracks_new_and_changed_notes(session: AsyncSession) -> None:
    """A changed note is re-indexed, so the index never goes stale."""
    project = await _project(session)
    note = await add_note(session, project_id=project.id, content="original wording")
    await session.commit()
    await index_notes(session, [note])

    assert await search_notes_fts(session, project.id, "original") != []

    note.content = "replaced wording"
    await session.commit()
    await index_notes(session, [note])

    assert await search_notes_fts(session, project.id, "original") == []
    assert await search_notes_fts(session, project.id, "replaced") != []


async def test_search_fts_respects_filters(session: AsyncSession) -> None:
    """The index honours the same filters as the substring search."""
    project = await _project(session)
    await add_note(
        session,
        project_id=project.id,
        content="shared keyword here",
        kind=NoteKind.insight,
    )
    await add_note(
        session,
        project_id=project.id,
        content="shared keyword there",
        kind=NoteKind.comment,
    )
    await session.commit()
    await rebuild_index(session)

    insights = await search_notes_fts(session, project.id, "shared", kind=NoteKind.insight)

    assert len(insights) == 1
    assert insights[0].kind is NoteKind.insight


async def test_search_fts_empty_query_returns_nothing(session: AsyncSession) -> None:
    """An empty query must not match everything."""
    project = await _project(session)
    await add_note(session, project_id=project.id, content="anything")
    await session.commit()
    await rebuild_index(session)

    assert await search_notes_fts(session, project.id, "   ") == []


async def test_search_notes_fts_and_substring_agree(session: AsyncSession) -> None:
    """Both search paths answer the same question the same way."""
    project = await _project(session)
    await add_note(session, project_id=project.id, content="alpha beta gamma")
    await add_note(session, project_id=project.id, content="delta epsilon")
    await session.commit()
    await rebuild_index(session)

    indexed = {note.content for note in await search_notes_fts(session, project.id, "alpha")}
    plain = {note.content for note in await search_notes(session, project.id, "alpha")}

    assert indexed == plain == {"alpha beta gamma"}


async def test_search_notes_keeps_substring_hits_the_index_cannot_find(
    session: AsyncSession,
) -> None:
    """The index must not lose matches that sit inside a word."""
    project = await _project(session)
    await add_note(session, project_id=project.id, content="Postgres migration blocked")
    await session.commit()
    await rebuild_index(session)

    # "gres" is inside the word: FTS5 cannot match it, the substring can.
    assert await search_notes_fts(session, project.id, "gres") == []

    results = await search_notes(session, project.id, "gres")

    assert len(results) == 1
    assert "Postgres" in results[0].content
