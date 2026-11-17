"""Agent repository: registry of MCP clients and humans (M7)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Agent, AgentRole


async def get_agent_by_name(session: AsyncSession, name: str) -> Agent | None:
    stmt = select(Agent).where(Agent.name == name)
    return (await session.execute(stmt)).scalar_one_or_none()


async def create_agent(
    session: AsyncSession,
    *,
    name: str,
    kind: str = "mcp-client",
    role: AgentRole = AgentRole.agent,
    meta: dict[str, Any] | None = None,
) -> Agent:
    agent = Agent(name=name, kind=kind, role=role, meta=meta or {})
    session.add(agent)
    await session.flush()
    return agent


async def get_or_create_agent(
    session: AsyncSession,
    *,
    name: str,
    kind: str = "mcp-client",
    role: AgentRole = AgentRole.agent,
) -> Agent:
    """Return an existing agent by name or create one."""

    existing = await get_agent_by_name(session, name)
    if existing is not None:
        return existing
    return await create_agent(session, name=name, kind=kind, role=role)
