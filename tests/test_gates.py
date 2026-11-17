"""Tests for workflow gates."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.repo.projects import create_project
from taskhub.repo.work_items import create_item
from taskhub.services.gates import check_done_gate, check_ready_gate


async def test_ready_gate_requires_estimate_and_rice(session: AsyncSession) -> None:
    project = await create_project(session, slug="p", name="P", root_path="/tmp/p")
    item = await create_item(session, project_id=project.id, title="Task")
    await session.commit()

    errors = await check_ready_gate(session, item)

    assert any("estimate_hours" in error for error in errors)
    assert any("meta.rice" in error for error in errors)


async def test_ready_gate_passes_with_estimate_and_rice(session: AsyncSession) -> None:
    project = await create_project(session, slug="p", name="P", root_path="/tmp/p")
    item = await create_item(
        session,
        project_id=project.id,
        title="Task",
        estimate_hours=4.0,
        meta={"rice": {"score": 60}},
    )
    await session.commit()

    errors = await check_ready_gate(session, item)

    assert errors == []


async def test_done_gate_requires_acceptance_criteria(session: AsyncSession) -> None:
    project = await create_project(session, slug="p", name="P", root_path="/tmp/p")
    item = await create_item(session, project_id=project.id, title="Task")
    await session.commit()

    errors = await check_done_gate(session, item)

    assert any("acceptance_criteria" in error for error in errors)
