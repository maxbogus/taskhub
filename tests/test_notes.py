"""Tests for note storage, listing and substring search (M6)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import NoteKind
from taskhub.repo.notes import add_note, list_notes, search_notes
from taskhub.repo.projects import create_project
from taskhub.repo.work_items import create_item


async def _project(session: AsyncSession):
    project = await create_project(session, slug="p", name="P", root_path="/tmp/p")
    await session.commit()
    return project


async def test_add_note_attaches_to_work_item(session: AsyncSession) -> None:
    project = await _project(session)
    item = await create_item(session, project_id=project.id, title="Task")
    await session.commit()

    note = await add_note(
        session,
        project_id=project.id,
        content="first attempt",
        work_item_id=item.id,
        kind=NoteKind.attempt,
        author="agent",
    )
    await session.commit()

    assert note.id
    assert note.work_item_id == item.id
    assert note.kind is NoteKind.attempt
    assert note.author == "agent"


async def test_list_notes_filters_by_item_and_kind(session: AsyncSession) -> None:
    project = await _project(session)
    item = await create_item(session, project_id=project.id, title="Task")
    await session.commit()
    await add_note(
        session, project_id=project.id, content="project note", kind=NoteKind.insight
    )
    await add_note(
        session,
        project_id=project.id,
        content="item note",
        work_item_id=item.id,
        kind=NoteKind.comment,
    )
    await session.commit()

    item_notes = await list_notes(session, project.id, work_item_id=item.id)
    assert [note.content for note in item_notes] == ["item note"]

    insights = await list_notes(session, project.id, kind=NoteKind.insight)
    assert [note.content for note in insights] == ["project note"]


async def test_search_notes_matches_substring(session: AsyncSession) -> None:
    project = await _project(session)
    await add_note(
        session, project_id=project.id, content="blocked by Postgres migration"
    )
    await add_note(session, project_id=project.id, content="unrelated note")
    await session.commit()

    results = await search_notes(session, project.id, "Postgres")

    assert len(results) == 1
    assert "Postgres" in results[0].content


async def test_search_notes_treats_wildcards_literally(
    session: AsyncSession,
) -> None:
    project = await _project(session)
    await add_note(session, project_id=project.id, content="coverage is 80% now")
    await add_note(session, project_id=project.id, content="nothing here")
    await session.commit()

    results = await search_notes(session, project.id, "80%")

    assert len(results) == 1
    assert results[0].content == "coverage is 80% now"


async def test_search_notes_empty_query_returns_nothing(
    session: AsyncSession,
) -> None:
    project = await _project(session)
    await add_note(session, project_id=project.id, content="anything")
    await session.commit()

    assert await search_notes(session, project.id, "   ") == []
