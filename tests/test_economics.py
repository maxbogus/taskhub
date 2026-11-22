"""Tests for the agent economics service (M11).

The economics layer answers "what did the agents actually cost?" and
"was that cheaper than doing it by hand?". Two rules are load-bearing and
tested here:

* an unpriced model contributes **no** cost but is reported as unpriced —
  never priced at zero, which would understate the cost;
* the reported ``Metric.cost_usd`` is kept separate from the estimated
  token cost, so a discrepancy between vendor billing and the price list
  stays visible.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Agent, Metric, ModelPrice, SessionSource, WorkStatus
from taskhub.models import Session as WorkSession
from taskhub.repo.projects import create_project
from taskhub.repo.work_items import create_item
from taskhub.services.economics import (
    cost_series,
    economics_report,
    economics_summary,
    money_evm,
    resolve_rates,
)


async def _seed(session: AsyncSession, *, slug: str = "p"):
    """Create a project with one done and one open item."""
    project = await create_project(
        session,
        slug=slug,
        name="P",
        root_path=f"/tmp/{slug}",
        config={"economics": {"human_rate_usd_per_hour": 50.0}},
    )
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
    open_item = await create_item(
        session,
        project_id=project.id,
        title="Open",
        status=WorkStatus.ready,
        estimate_hours=6.0,
    )
    open_item.created_at = datetime(2026, 1, 2, 8)
    open_item.due_at = datetime(2026, 1, 1, 12)
    agent = Agent(name="econ-agent")
    session.add(agent)
    await session.flush()
    return project, done, open_item, agent


async def _add_price(
    session: AsyncSession,
    *,
    pattern: str,
    input_micro: int,
    output_micro: int = 0,
    valid_from: datetime = datetime(2026, 1, 1),
) -> None:
    """Insert a price row directly."""
    session.add(
        ModelPrice(
            pattern=pattern,
            currency="USD",
            input_usd_micro=input_micro,
            output_usd_micro=output_micro,
            cache_read_usd_micro=0,
            cache_write_usd_micro=0,
            valid_from=valid_from,
        )
    )
    await session.flush()


async def _add_metric(
    session: AsyncSession,
    *,
    work_item_id: str,
    agent_id: str,
    project_id: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    active_ms: int = 0,
    cost_usd: float | None = None,
    success: bool = True,
    recorded_at: datetime = datetime(2026, 1, 5, 10),
) -> None:
    """Insert a metric row with an explicit timestamp."""
    work_session = WorkSession(
        project_id=project_id,
        work_item_id=work_item_id,
        agent_id=agent_id,
        source=SessionSource.manual,
        model=model,
        started_at=recorded_at,
        ended_at=recorded_at,
    )
    session.add(work_session)
    await session.flush()
    session.add(
        Metric(
            session_id=work_session.id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            active_ms=active_ms,
            cost_usd=cost_usd,
            success=success,
            recorded_at=recorded_at,
        )
    )
    await session.flush()


async def test_resolve_rates_prefers_project_override(session: AsyncSession) -> None:
    """Project config wins over the global default."""
    project, *_ = await _seed(session)

    rates = resolve_rates(project)

    assert rates.human_rate_usd_per_hour == Decimal("50")
    assert rates.source == "project"


async def test_resolve_rates_falls_back_to_settings(session: AsyncSession) -> None:
    """Without an override the configured default applies."""
    project, *_ = await _seed(session)
    project.config = {}

    rates = resolve_rates(project)

    assert rates.human_rate_usd_per_hour > 0
    assert rates.source == "settings"


async def test_economics_report_prices_tokens_from_price_list(
    session: AsyncSession,
) -> None:
    """Token cost comes from the price list, not from the reported cost."""
    project, done, _open_item, agent = await _seed(session)
    await _add_price(session, pattern="econ-*", input_micro=1_000_000, output_micro=1_000_000)
    await _add_metric(
        session,
        work_item_id=done.id,
        agent_id=agent.id,
        project_id=project.id,
        model="econ-small",
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
        active_ms=3_600_000,
        cost_usd=0.5,
    )
    await session.commit()

    report = await economics_report(session, project_slug="p")

    assert len(report) == 1
    row = report[0]
    assert row["key"] == "econ-small"
    assert row["token_cost_usd"] == pytest.approx(2.0)
    assert row["reported_cost_usd"] == pytest.approx(0.5)
    assert row["active_hours"] == pytest.approx(1.0)
    assert row["unpriced"] is False


async def test_economics_report_marks_unpriced_models(session: AsyncSession) -> None:
    """A model without a price is reported, not priced at zero."""
    project, done, _open_item, agent = await _seed(session)
    await _add_metric(
        session,
        work_item_id=done.id,
        agent_id=agent.id,
        project_id=project.id,
        model="mystery-model",
        prompt_tokens=1000,
    )
    await session.commit()

    report = await economics_report(session, project_slug="p")

    assert report[0]["unpriced"] is True
    assert report[0]["token_cost_usd"] is None


async def test_economics_report_counts_failures(session: AsyncSession) -> None:
    """Failed runs are counted and priced as errors."""
    project, done, _open_item, agent = await _seed(session)
    await _add_price(session, pattern="econ-*", input_micro=1_000_000)
    await _add_metric(
        session,
        work_item_id=done.id,
        agent_id=agent.id,
        project_id=project.id,
        model="econ-small",
        prompt_tokens=1_000_000,
        success=False,
    )
    await session.commit()

    report = await economics_report(session, project_slug="p")

    assert report[0]["failures"] == 1
    assert report[0]["error_rate"] == pytest.approx(1.0)


async def test_economics_summary_totals_and_savings(session: AsyncSession) -> None:
    """The summary compares agent spend with the human baseline."""
    project, done, _open_item, agent = await _seed(session)
    await _add_price(session, pattern="econ-*", input_micro=1_000_000)
    await _add_metric(
        session,
        work_item_id=done.id,
        agent_id=agent.id,
        project_id=project.id,
        model="econ-small",
        prompt_tokens=1_000_000,
        active_ms=3_600_000,
    )
    await session.commit()

    summary = await economics_summary(session, project_slug="p")

    # 10 estimated hours * 50 USD/h
    assert summary["baseline_human_cost_usd"] == pytest.approx(500.0)
    assert summary["token_cost_usd"] == pytest.approx(1.0)
    assert summary["total_cost_usd"] == pytest.approx(1.0)
    assert summary["savings_usd"] == pytest.approx(499.0)
    assert summary["roi"] == pytest.approx(499.0)
    assert summary["human_rate_usd_per_hour"] == pytest.approx(50.0)


async def test_economics_summary_roi_is_none_without_cost(
    session: AsyncSession,
) -> None:
    """Zero spend must not produce a fake ROI."""
    _project, *_ = await _seed(session)
    await session.commit()

    summary = await economics_summary(session, project_slug="p")

    assert summary["total_cost_usd"] == pytest.approx(0.0)
    assert summary["roi"] is None


async def test_economics_summary_reports_unpriced_share(session: AsyncSession) -> None:
    """Unpriced tokens are surfaced so the total is not trusted blindly."""
    project, done, _open_item, agent = await _seed(session)
    await _add_metric(
        session,
        work_item_id=done.id,
        agent_id=agent.id,
        project_id=project.id,
        model="mystery-model",
        prompt_tokens=500,
    )
    await session.commit()

    summary = await economics_summary(session, project_slug="p")

    assert summary["unpriced_models"] == ["mystery-model"]
    assert summary["unpriced_token_share"] == pytest.approx(1.0)


async def test_cost_series_buckets_by_day(session: AsyncSession) -> None:
    """Usage is grouped into daily buckets with a running total."""
    project, done, _open_item, agent = await _seed(session)
    await _add_price(session, pattern="econ-*", input_micro=1_000_000)
    await _add_metric(
        session,
        work_item_id=done.id,
        agent_id=agent.id,
        project_id=project.id,
        model="econ-small",
        prompt_tokens=1_000_000,
        recorded_at=datetime(2026, 1, 5, 10),
    )
    await _add_metric(
        session,
        work_item_id=done.id,
        agent_id=agent.id,
        project_id=project.id,
        model="econ-small",
        prompt_tokens=2_000_000,
        recorded_at=datetime(2026, 1, 6, 10),
    )
    await session.commit()

    series = await cost_series(session, project_slug="p", bucket="day")

    assert series["bucket"] == "day"
    points = series["points"]
    assert [point["bucket"] for point in points] == ["2026-01-05", "2026-01-06"]
    assert points[0]["total_cost_usd"] == pytest.approx(1.0)
    assert points[1]["total_cost_usd"] == pytest.approx(2.0)
    assert points[1]["cumulative_usd"] == pytest.approx(3.0)
    assert series["burn_usd_per_day"] == pytest.approx(1.5)


async def test_cost_series_bucket_week(session: AsyncSession) -> None:
    """Weekly buckets use ISO weeks."""
    project, done, _open_item, agent = await _seed(session)
    await _add_price(session, pattern="econ-*", input_micro=1_000_000)
    await _add_metric(
        session,
        work_item_id=done.id,
        agent_id=agent.id,
        project_id=project.id,
        model="econ-small",
        prompt_tokens=1_000_000,
        recorded_at=datetime(2026, 1, 5, 10),
    )
    await session.commit()

    series = await cost_series(session, project_slug="p", bucket="week")

    assert series["bucket"] == "week"
    assert series["points"][0]["bucket"].startswith("2026-W02")


async def test_cost_series_rejects_unknown_bucket(session: AsyncSession) -> None:
    """An unsupported bucket is a clear error, not a silent default."""
    with pytest.raises(ValueError, match="bucket"):
        await cost_series(session, project_slug="p", bucket="fortnight")


def test_money_evm_uses_money_scale() -> None:
    """CPI compares human value against real spend."""
    result = money_evm(
        earned_value_usd=Decimal("200"),
        actual_cost_usd=Decimal("100"),
        budget_at_completion_usd=Decimal("300"),
        planned_value_usd=Decimal("300"),
    )

    assert result["earned_value_usd"] == pytest.approx(200.0)
    assert result["actual_cost_usd"] == pytest.approx(100.0)
    assert result["cpi"] == pytest.approx(2.0)
    assert result["eac_usd"] == pytest.approx(150.0)
    assert result["vac_usd"] == pytest.approx(150.0)


def test_money_evm_handles_zero_actual_cost() -> None:
    """Zero spend yields no CPI instead of a division by zero."""
    result = money_evm(
        earned_value_usd=Decimal("200"),
        actual_cost_usd=Decimal("0"),
        budget_at_completion_usd=Decimal("300"),
        planned_value_usd=None,
    )

    assert result["cpi"] is None
    assert result["eac_usd"] == pytest.approx(300.0)
