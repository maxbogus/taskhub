"""Tests for workflow transitions, gates and role permissions."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Note, WorkStatus
from taskhub.repo.projects import create_project
from taskhub.repo.work_items import add_dependency, create_item
from taskhub.services.gates import FlowError, change_status, workflow_info

RICE = {"rice": {"R": 10, "I": 2, "C": 0.8, "E": 4}}


async def _project(session: AsyncSession, slug: str = "p"):
    project = await create_project(session, slug=slug, name=slug, root_path=f"/tmp/{slug}")
    await session.commit()
    return project


async def test_ready_requires_estimate_and_rice(session: AsyncSession) -> None:
    project = await _project(session)
    item = await create_item(session, project_id=project.id, title="Task")
    await session.commit()

    with pytest.raises(FlowError, match="estimate_hours"):
        await change_status(session, item.id, "ready", role="agent")

    item.estimate_hours = 4.0
    item.meta = RICE
    await session.commit()

    result = await change_status(session, item.id, "ready", role="agent", actor="cline-agent")
    await session.commit()

    assert result.from_status == "backlog"
    assert result.to_status == "ready"
    assert item.status is WorkStatus.ready
    notes = list((await session.execute(select(Note))).scalars().all())
    assert len(notes) == 1
    assert notes[0].author == "cline-agent"
    assert notes[0].meta["from"] == "backlog"
    assert notes[0].meta["to"] == "ready"


async def test_ready_is_blocked_by_unfinished_dependency(
    session: AsyncSession,
) -> None:
    project = await _project(session)
    blocker = await create_item(session, project_id=project.id, title="Blocker")
    blocked = await create_item(
        session,
        project_id=project.id,
        title="Blocked",
        estimate_hours=4.0,
        meta=RICE,
    )
    await add_dependency(session, from_id=blocker.id, to_id=blocked.id)
    await session.commit()

    with pytest.raises(FlowError, match="blocked by"):
        await change_status(session, blocked.id, "ready", role="agent")


async def test_agent_cannot_complete_review(session: AsyncSession) -> None:
    project = await _project(session)
    item = await create_item(
        session,
        project_id=project.id,
        title="Review me",
        status=WorkStatus.review,
        acceptance_criteria="- [x] done",
    )
    item.started_at = datetime(2026, 1, 1, 10)
    await session.commit()

    with pytest.raises(FlowError, match="not allowed"):
        await change_status(session, item.id, "done", role="agent")

    result = await change_status(session, item.id, "done", role="reviewer", actor="reviewer-1")
    await session.commit()

    assert result.to_status == "done"
    assert item.status is WorkStatus.done
    assert item.done_at is not None


async def test_done_requires_acceptance_criteria(
    session: AsyncSession,
) -> None:
    project = await _project(session)
    item = await create_item(
        session, project_id=project.id, title="No criteria", status=WorkStatus.review
    )
    item.started_at = datetime(2026, 1, 1, 10)
    await session.commit()

    with pytest.raises(FlowError, match="acceptance_criteria"):
        await change_status(session, item.id, "done", role="reviewer")


async def test_human_can_reopen_and_archive(session: AsyncSession) -> None:
    project = await _project(session)
    item = await create_item(
        session,
        project_id=project.id,
        title="Finished",
        status=WorkStatus.done,
        acceptance_criteria="- [x] done",
        estimate_hours=4.0,
        meta=RICE,
    )
    item.done_at = datetime(2026, 1, 1, 12)
    await session.commit()

    await change_status(session, item.id, "ready", role="human")
    await session.commit()
    assert item.status is WorkStatus.ready
    assert item.done_at is None

    await change_status(session, item.id, "in_progress", role="human")
    await change_status(session, item.id, "review", role="human")
    await change_status(session, item.id, "done", role="human")
    await change_status(session, item.id, "archived", role="human")
    await session.commit()

    assert item.status is WorkStatus.archived
    assert item.archived_at is not None


async def test_workflow_info_lists_allowed_and_gates(
    session: AsyncSession,
) -> None:
    project = await _project(session)
    item = await create_item(session, project_id=project.id, title="Task")
    await session.commit()

    info = await workflow_info(session, item.id, "agent")

    assert info["status"] == "backlog"
    assert info["allowed"] == ["ready"]
    assert "estimate_hours" in " ".join(info["gates"]["ready"])


async def test_project_flow_override(session: AsyncSession) -> None:
    project = await create_project(
        session,
        slug="override",
        name="Override",
        root_path="/tmp/override",
        config={"flow": {"roles": {"agent": ["backlog->in_progress"]}}},
    )
    item = await create_item(session, project_id=project.id, title="Task")
    await session.commit()

    info = await workflow_info(session, item.id, "agent")

    assert info["allowed"] == ["in_progress"]
