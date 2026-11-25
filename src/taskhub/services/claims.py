"""TTL claim leases: acquire, renew and release (M7)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Agent, Claim, WorkItem
from taskhub.repo.agents import get_agent_by_name, get_or_create_agent
from taskhub.repo.claims import (
    claim_item,
    get_active_claim,
    release_item,
    renew_claim,
)
from taskhub.repo.work_items import get_item


class ClaimError(ValueError):
    """Raised when a claim cannot be acquired."""


@dataclass(frozen=True)
class ClaimResult:
    """Outcome of an acquire/renew call."""

    claim: Claim
    item: WorkItem
    agent: Agent
    renewed: bool


async def acquire_claim(
    session: AsyncSession,
    *,
    item_id: str,
    agent_name: str,
    ttl_minutes: int | None = None,
) -> ClaimResult:
    """Acquire a TTL lease, renewing it when the same agent already holds it."""

    item = await get_item(session, item_id)
    if item is None:
        raise ClaimError(f"work item '{item_id}' not found")
    agent = await get_or_create_agent(session, name=agent_name)
    active = await get_active_claim(session, item_id)
    if active is not None:
        if active.agent_id != agent.id:
            raise ClaimError(f"item '{item_id}' is already claimed")
        renewed = await renew_claim(session, active, ttl_minutes=ttl_minutes)
        return ClaimResult(claim=renewed, item=item, agent=agent, renewed=True)
    claim = await claim_item(
        session, work_item_id=item_id, agent_id=agent.id, ttl_minutes=ttl_minutes
    )
    return ClaimResult(claim=claim, item=item, agent=agent, renewed=False)


async def release_claim(session: AsyncSession, *, item_id: str, agent_name: str) -> int:
    """Release all active leases held by the agent on an item."""

    agent = await get_agent_by_name(session, agent_name)
    if agent is None:
        return 0
    return await release_item(session, work_item_id=item_id, agent_id=agent.id)
