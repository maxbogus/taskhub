"""Tests for the single evaluation entry point."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Agent, Metric, SessionSource, WorkStatus
from taskhub.models import Session as WorkSession
from taskhub.repo.projects import create_project
from taskhub.repo.work_items import create_item
from taskhub.services.evaluation import evaluate_project


async def test_evaluate_project_returns_all_sections(session: AsyncSession) -> None:
    project = await create_project(
        session,
        slug="p",
        name="P",
        root_path="/tmp/p",
        config={"forecast": {"velocity_hours_per_week": 10.0}},
    )
    done = await create_item(
        session,
        project_id=project.id,
        title="Done",
        status=WorkStatus.done,
        estimate_hours=4.0,
        meta={"rice": {"R": 10, "I": 2, "C": 0.8, "E": 4}},
    )
    done.started_at = datetime(2026, 1, 1, 10)
    done.done_at = datetime(2026, 1, 1, 14)
    await create_item(
        session,
        project_id=project.id,
        title="Open",
        estimate_hours=6.0,
        meta={"rice": {"R": 8, "I": 1, "C": 0.5, "E": 6}},
    )
    agent = Agent(name="agent-eval")
    session.add(agent)
    await session.flush()
    work_session = WorkSession(
        work_item_id=done.id, agent_id=agent.id, source=SessionSource.manual
    )
    session.add(work_session)
    await session.flush()
    session.add(Metric(session_id=work_session.id, active_ms=2 * 3_600_000))
    await session.commit()

    evaluation = await evaluate_project(
        session, project, n_iterations=200, random_seed=42
    )

    assert evaluation["project"] == "p"
    assert evaluation["items"]["total"] == 2
    assert evaluation["items"]["completed"] == 1
    assert evaluation["progress"]["earned_value_hours"] == 4.0
    assert evaluation["progress"]["budget_at_completion_hours"] == 6.0
    assert evaluation["progress"]["actual_cost_hours"] == 2.0
    assert evaluation["progress"]["cpi"] == 2.0
    assert evaluation["forecast"]["status"] == "ok"
    assert evaluation["forecast"]["p50"] >= 0
    assert evaluation["prioritization"]["top"][0]["score"] == 4.0
    assert evaluation["health"]["missing_estimate"] == 0


async def test_evaluate_project_without_remaining_work(session: AsyncSession) -> None:
    project = await create_project(
        session, slug="done-project", name="Done", root_path="/tmp/done"
    )
    await create_item(
        session,
        project_id=project.id,
        title="Done",
        status=WorkStatus.done,
        estimate_hours=4.0,
    )
    await session.commit()

    evaluation = await evaluate_project(session, project, n_iterations=100)

    assert evaluation["forecast"]["status"] == "no_remaining_work"
    assert evaluation["forecast"]["p50"] == 0.0
