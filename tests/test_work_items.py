"""Tests for work item selection, dependencies and claims."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Agent, WorkStatus
from taskhub.repo.claims import claim_item
from taskhub.repo.projects import create_project
from taskhub.repo.work_items import add_dependency, create_item, next_item


async def test_get_next_item_returns_highest_priority(session: AsyncSession) -> None:
    project = await create_project(session, slug="p", name="P", root_path="/tmp/p")
    await create_item(session, project_id=project.id, title="Low", priority=4)
    first = await create_item(session, project_id=project.id, title="First", priority=1)
    await session.commit()

    result = await next_item(session, project.id)

    assert result is not None
    assert result.id == first.id


async def test_get_next_item_skips_blocked_items(session: AsyncSession) -> None:
    project = await create_project(session, slug="p", name="P", root_path="/tmp/p")
    blocker = await create_item(
        session, project_id=project.id, title="Blocker", priority=2
    )
    await create_item(
        session,
        project_id=project.id,
        title="Blocked",
        priority=1,
        status=WorkStatus.ready,
    )
    # Find the blocked item id through the dependency target.
    blocked = await next_item(session, project.id)
    assert blocked is not None
    # Block the highest-priority item, so the blocker must be returned instead.
    from sqlalchemy import select

    from taskhub.models import WorkItem

    blocked_item = (
        await session.execute(select(WorkItem).where(WorkItem.title == "Blocked"))
    ).scalar_one()
    await add_dependency(session, from_id=blocker.id, to_id=blocked_item.id)
    await session.commit()

    result = await next_item(session, project.id)

    assert result is not None
    assert result.id == blocker.id


async def test_get_next_item_skips_active_claims(session: AsyncSession) -> None:
    project = await create_project(session, slug="p", name="P", root_path="/tmp/p")
    item = await create_item(session, project_id=project.id, title="Claimed")
    agent = Agent(name="agent-1")
    session.add(agent)
    await session.flush()
    await claim_item(session, work_item_id=item.id, agent_id=agent.id)
    await session.commit()

    result = await next_item(session, project.id)

    assert result is None
