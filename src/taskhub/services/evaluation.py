"""Single evaluation entry point: RICE + EVM + Monte Carlo + health."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Project, WorkItem, WorkStatus
from taskhub.services.economics import money_progress
from taskhub.services.evm import EVMCalculator
from taskhub.services.forecast import simulate_duration, summarize_forecast
from taskhub.services.metrics import collect_project_metrics
from taskhub.services.prioritizer import (
    rice_category,
    rice_recommendation,
    rice_score,
)

DEFAULT_TARGETS_WEEKS = (4, 8, 12, 16, 24)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _rice_entry(item: WorkItem) -> dict[str, Any] | None:
    rice = (item.meta or {}).get("rice")
    if not rice:
        return None
    effort = float(rice.get("E", rice.get("effort", item.estimate_hours or 1.0)))
    score = rice.get("score")
    if score is None:
        score = rice_score(
            float(rice.get("R", rice.get("reach", 100))),
            float(rice.get("I", rice.get("impact", 1))),
            float(rice.get("C", rice.get("confidence", 0.75))),
            effort,
        )
    numeric_score = float(score)
    return {
        "id": item.id,
        "title": item.title,
        "score": numeric_score,
        "category": rice_category(numeric_score),
        "recommendation": rice_recommendation(numeric_score),
    }


def _calibrate_velocity(done: list[WorkItem], config: dict[str, Any], now: datetime) -> float:
    configured = config.get("velocity_hours_per_week")
    if configured:
        return float(configured)
    estimated_done = sum(item.estimate_hours or 0.0 for item in done if item.done_at)
    done_dates: list[datetime] = [item.done_at for item in done if item.done_at is not None]
    if estimated_done <= 0 or not done_dates:
        return 10.0
    first = min(done_dates)
    weeks = max(1.0, (now - first).total_seconds() / (7 * 24 * 3600))
    return max(1.0, estimated_done / weeks)


async def evaluate_project(
    session: AsyncSession,
    project: Project,
    *,
    n_iterations: int = 2000,
    random_seed: int = 42,
    targets_weeks: list[int] | None = None,
) -> dict[str, Any]:
    """Evaluate a project on one snapshot: RICE, EVM, forecast and health."""

    items = list(
        (await session.execute(select(WorkItem).where(WorkItem.project_id == project.id)))
        .scalars()
        .all()
    )
    metrics = await collect_project_metrics(session, project.id)
    now = _utcnow()

    ranked = sorted(
        (entry for entry in (_rice_entry(item) for item in items) if entry is not None),
        key=lambda entry: entry["score"],
        reverse=True,
    )
    next_sprint = [
        entry["id"] for entry in ranked if entry["recommendation"] in ("Do first", "Schedule")
    ][:5]

    done = [item for item in items if item.status is WorkStatus.done]
    open_items = [
        item
        for item in items
        if item.status not in (WorkStatus.done, WorkStatus.cancelled, WorkStatus.archived)
    ]

    earned_value = round(sum(item.estimate_hours or 0.0 for item in done), 4)
    budget_at_completion = round(sum(item.estimate_hours or 0.0 for item in open_items), 4)
    actual_cost = metrics.active_time_hours
    planned_value = round(
        sum(
            item.estimate_hours or 0.0
            for item in open_items
            if item.due_at is not None and item.due_at <= now
        ),
        4,
    )

    evm = EVMCalculator().evaluate(earned_value, actual_cost, budget_at_completion, planned_value)
    progress = {
        "earned_value_hours": earned_value,
        "actual_cost_hours": actual_cost,
        "budget_at_completion_hours": budget_at_completion,
        "planned_value_hours": planned_value,
        "cpi": float(evm.cpi) if evm.cpi is not None else None,
        "spi": float(evm.spi) if evm.spi is not None else None,
        "eac_hours": float(evm.eac),
        "etc_hours": float(evm.etc),
        "vac_hours": float(evm.vac),
    }

    config: dict[str, Any] = {}
    if project.config:
        raw_config = project.config.get("forecast")
        if isinstance(raw_config, dict):
            config = raw_config
    blocked_count = sum(1 for item in open_items if item.status is WorkStatus.blocked)
    velocity = _calibrate_velocity(done, config, now)

    if budget_at_completion <= 0:
        forecast: dict[str, Any] = {
            "status": "no_remaining_work",
            "remaining_hours": 0.0,
            "p50": 0.0,
            "p90": 0.0,
            "probability_by_target": {},
        }
    else:
        simulation = simulate_duration(
            sp_total_min=budget_at_completion * 0.8,
            sp_total_mode=budget_at_completion,
            sp_total_max=budget_at_completion * 1.5,
            velocity_per_fte_per_week=velocity,
            fte_count=1.0,
            velocity_noise_pct=float(config.get("velocity_noise_pct", 0.2)),
            blocker_lambda=max(0.5, float(blocked_count)),
            blocker_days_lost=float(config.get("blocker_days_lost", 3.0)),
            sick_day_prob=float(config.get("sick_day_prob", 0.05)),
            n_iterations=n_iterations,
            random_seed=random_seed,
        )
        forecast = {
            "status": "ok",
            "remaining_hours": budget_at_completion,
            "velocity_hours_per_week": round(velocity, 4),
            **summarize_forecast(simulation, list(targets_weeks or DEFAULT_TARGETS_WEEKS)),
        }

    money_evaluation = await money_progress(
        session,
        project,
        earned_value_hours=earned_value,
        budget_at_completion_hours=budget_at_completion,
        planned_value_hours=planned_value,
    )

    stale_cutoff = now - timedelta(days=7)
    health = {
        "blocked_items": blocked_count,
        "stale_in_progress": sum(
            1
            for item in open_items
            if item.status is WorkStatus.in_progress
            and item.started_at is not None
            and item.started_at < stale_cutoff
        ),
        "missing_estimate": sum(1 for item in open_items if item.estimate_hours is None),
        "variance_ratio": metrics.variance_ratio,
    }

    return {
        "project": project.slug,
        "generated_at": now.isoformat(),
        "items": {
            "total": metrics.total_items,
            "completed": metrics.completed_items,
            "by_status": metrics.by_status,
        },
        "metrics": metrics.to_dict(),
        "prioritization": {"top": ranked[:10], "next_sprint": next_sprint},
        "progress": progress,
        "progress_money": money_evaluation,
        "forecast": forecast,
        "health": health,
    }
