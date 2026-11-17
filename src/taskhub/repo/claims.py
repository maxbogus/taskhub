"""Claim (TTL lease) repository."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.config import get_settings
from taskhub.models import Claim


async def claim_item(
    session: AsyncSession,
    *,
    work_item_id: str,
    agent_id: str,
    ttl_minutes: int | None = None,
    now: datetime | None = None,
) -> Claim:
    now = now or datetime.now(UTC).replace(tzinfo=None)
    ttl = ttl_minutes or get_settings().default_claim_ttl_minutes
    claim = Claim(
        work_item_id=work_item_id,
        agent_id=agent_id,
        acquired_at=now,
        expires_at=now + timedelta(minutes=ttl),
    )
    session.add(claim)
    await session.flush()
    return claim


async def release_item(
    session: AsyncSession,
    *,
    work_item_id: str,
    agent_id: str,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(UTC).replace(tzinfo=None)
    stmt = select(Claim).where(
        Claim.work_item_id == work_item_id,
        Claim.agent_id == agent_id,
        Claim.released_at.is_(None),
    )
    claims = list((await session.execute(stmt)).scalars().all())
    for claim in claims:
        claim.released_at = now
    return len(claims)
