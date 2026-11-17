"""Tests for automatic project metrics."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Agent, Metric, SessionSource, WorkStatus
from taskhub.models import Session as WorkSession
from taskhub.repo.projects import create_project
from taskhub.repo.work_items import create_item
from taskhub.services.metrics import collect_project_metrics


async def test_collect_project_metrics(session: AsyncSession) -> None:
    project = await create_project(session, slug="p", name="P", root_path="/tmp/p")
    done = await create_item(
        session,
        project_id=project.id,
        title="Done",
        status=WorkStatus.done,
        estimate_hours=4.0,
    )
    done.created_at = datetime(2026, 1, 1, 8)
    done.started_at = datetime(2026, 1, 1, 9)
    done.done_at = datetime(2026, 1, 1, 11)
    await create_item(
        session, project_id=project.id, title="Open", estimate_hours=6.0
    )
    agent = Agent(name="agent-metrics")
    session.add(agent)
    await session.flush()
    work_session = WorkSession(
        work_item_id=done.id,
        agent_id=agent.id,
        source=SessionSource.manual,
        model="test-model",
    )
    session.add(work_session)
    await session.flush()
    session.add(
        Metric(
            session_id=work_session.id,
            active_ms=2 * 3_600_000,
            prompt_tokens=100,
            completion_tokens=50,
            cache_read_tokens=10,
            cache_write_tokens=5,
            cost_usd=0.42,
            model="test-model",
        )
    )
    await session.commit()

    metrics = await collect_project_metrics(session, project.id)

    assert metrics.total_items == 2
    assert metrics.completed_items == 1
    assert metrics.by_status["done"] == 1
    assert metrics.by_status["backlog"] == 1
    assert metrics.estimate_hours_total == 10.0
    assert metrics.estimate_hours_done == 4.0
    assert metrics.estimate_hours_open == 6.0
    assert metrics.active_time_hours == 2.0
    assert metrics.actual_hours_done == 2.0
    assert metrics.variance_ratio == 0.5
    assert metrics.median_cycle_time_hours == 2.0
    assert metrics.median_lead_time_hours == 3.0
    assert metrics.prompt_tokens == 100
    assert metrics.completion_tokens == 50
    assert metrics.total_tokens == 150
    assert metrics.cache_read_tokens == 10
    assert metrics.cache_write_tokens == 5
    assert metrics.cost_usd == 0.42
