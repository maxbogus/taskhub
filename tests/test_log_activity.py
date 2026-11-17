"""Tests for manual log_activity and metric aggregation."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Agent
from taskhub.repo.projects import create_project
from taskhub.repo.work_items import create_item
from taskhub.services.metric_ingest import log_activity, metrics_report, task_metrics


async def _seed(session: AsyncSession):
    project = await create_project(session, slug="p", name="P", root_path="/tmp/p")
    item = await create_item(session, project_id=project.id, title="Task")
    await session.commit()
    return project, item


async def test_log_activity_aggregates_by_task_and_model(
    session: AsyncSession,
) -> None:
    _, item = await _seed(session)

    first = await log_activity(
        session,
        project_slug="p",
        work_item_id=item.id,
        agent_name="cline-agent",
        source="cline",
        external_id="run-1",
        model="MiniMax-M2.7",
        prompt_tokens=10,
        completion_tokens=5,
        active_ms=3_600_000,
        cost_usd=0.1,
    )
    second = await log_activity(
        session,
        project_slug="p",
        work_item_id=item.id,
        agent_name="cline-agent",
        source="cline",
        external_id="run-1",
        model="MiniMax-M2.7",
        prompt_tokens=20,
        completion_tokens=10,
        active_ms=3_600_000,
        cost_usd=0.2,
    )

    assert first["session_id"] == second["session_id"]
    agents = list((await session.execute(select(Agent))).scalars().all())
    assert len(agents) == 1

    metrics = await task_metrics(session, item.id)
    assert metrics["sessions"] == 1
    assert metrics["prompt_tokens"] == 30
    assert metrics["completion_tokens"] == 15
    assert metrics["active_hours"] == 2.0
    assert metrics["cost_usd"] == pytest.approx(0.3)
    assert metrics["by_model"]["MiniMax-M2.7"]["prompt_tokens"] == 30


async def test_metrics_report_group_by_model_and_project(
    session: AsyncSession,
) -> None:
    await _seed(session)
    await log_activity(
        session,
        project_slug="p",
        agent_name="agent-a",
        source="manual",
        external_id="run-2",
        model="model-a",
        prompt_tokens=100,
        completion_tokens=50,
        active_ms=1_800_000,
        cost_usd=0.25,
    )

    report = await metrics_report(session, project_slug="p", group_by="model")

    assert len(report) == 1
    assert report[0]["key"] == "model-a"
    assert report[0]["sessions"] == 1
    assert report[0]["prompt_tokens"] == 100
    assert report[0]["active_hours"] == 0.5
    assert report[0]["cost_usd"] == pytest.approx(0.25)

    by_agent = await metrics_report(session, project_slug="p", group_by="agent")
    assert by_agent[0]["key"] == "agent-a"
