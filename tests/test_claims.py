"""Tests for TTL claim leases and agent registration (M7)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import AgentRole
from taskhub.repo.claims import claim_item, list_active_claims
from taskhub.repo.projects import create_project
from taskhub.repo.work_items import create_item
from taskhub.services.claims import ClaimError, acquire_claim, release_claim


async def _project(session: AsyncSession):
    project = await create_project(session, slug="p", name="P", root_path="/tmp/p")
    await session.commit()
    return project


async def test_acquire_creates_agent_and_claim(session: AsyncSession) -> None:
    project = await _project(session)
    item = await create_item(session, project_id=project.id, title="Task")
    await session.commit()

    result = await acquire_claim(session, item_id=item.id, agent_name="agent-1")
    await session.commit()

    assert result.renewed is False
    assert result.agent.name == "agent-1"
    assert result.agent.role is AgentRole.agent
    assert result.claim.work_item_id == item.id
    assert result.claim.expires_at > result.claim.acquired_at


async def test_same_agent_reacquire_renews(session: AsyncSession) -> None:
    project = await _project(session)
    item = await create_item(session, project_id=project.id, title="Task")
    await session.commit()

    first = await acquire_claim(session, item_id=item.id, agent_name="agent-1")
    await session.commit()
    second = await acquire_claim(session, item_id=item.id, agent_name="agent-1")
    await session.commit()

    assert second.renewed is True
    assert second.claim.id == first.claim.id


async def test_other_agent_cannot_claim(session: AsyncSession) -> None:
    project = await _project(session)
    item = await create_item(session, project_id=project.id, title="Task")
    await session.commit()
    await acquire_claim(session, item_id=item.id, agent_name="agent-1")
    await session.commit()

    with pytest.raises(ClaimError, match="already claimed"):
        await acquire_claim(session, item_id=item.id, agent_name="agent-2")


async def test_expired_claim_does_not_block(session: AsyncSession) -> None:
    project = await _project(session)
    item = await create_item(session, project_id=project.id, title="Task")
    await session.commit()

    from taskhub.repo.agents import get_or_create_agent

    agent = await get_or_create_agent(session, name="agent-1")
    await claim_item(
        session, work_item_id=item.id, agent_id=agent.id, ttl_minutes=-1
    )
    await session.commit()

    result = await acquire_claim(session, item_id=item.id, agent_name="agent-2")
    await session.commit()

    assert result.agent.name == "agent-2"
    assert result.renewed is False


async def test_release_then_other_agent_can_claim(session: AsyncSession) -> None:
    project = await _project(session)
    item = await create_item(session, project_id=project.id, title="Task")
    await session.commit()
    await acquire_claim(session, item_id=item.id, agent_name="agent-1")
    await session.commit()

    released = await release_claim(session, item_id=item.id, agent_name="agent-1")
    await session.commit()
    assert released == 1

    result = await acquire_claim(session, item_id=item.id, agent_name="agent-2")
    await session.commit()
    assert result.agent.name == "agent-2"


async def test_release_unknown_agent_is_noop(session: AsyncSession) -> None:
    project = await _project(session)
    item = await create_item(session, project_id=project.id, title="Task")
    await session.commit()

    assert await release_claim(session, item_id=item.id, agent_name="ghost") == 0


async def test_list_active_claims_filters_by_project(
    session: AsyncSession,
) -> None:
    project = await _project(session)
    other = await create_project(
        session, slug="q", name="Q", root_path="/tmp/q"
    )
    item = await create_item(session, project_id=project.id, title="Task")
    other_item = await create_item(session, project_id=other.id, title="Other")
    await session.commit()

    await acquire_claim(session, item_id=item.id, agent_name="agent-1")
    await acquire_claim(session, item_id=other_item.id, agent_name="agent-1")
    await session.commit()

    scoped = await list_active_claims(session, project_id=project.id)
    assert [claim.work_item_id for claim in scoped] == [item.id]

    everything = await list_active_claims(session)
    assert len(everything) == 2
