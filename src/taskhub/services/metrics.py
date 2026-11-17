"""Automatic project metrics computed from the database."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Metric, WorkItem, WorkStatus
from taskhub.models import Session as WorkSession


@dataclass(frozen=True)
class ProjectMetrics:
    """Aggregated counters and derived metrics for one project."""

    total_items: int
    completed_items: int
    by_status: dict[str, int]
    active_time_hours: float
    estimate_hours_total: float
    estimate_hours_done: float
    estimate_hours_open: float
    actual_hours_done: float
    variance_ratio: float | None
    median_cycle_time_hours: float | None
    median_lead_time_hours: float | None
    prompt_tokens: int
    completion_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    total_tokens: int
    cost_usd: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _hours(delta_seconds: float) -> float:
    return round(delta_seconds / 3600.0, 4)


def _ms_to_hours(milliseconds: float) -> float:
    return round(milliseconds / 3_600_000.0, 4)


def _median_hours(values: list[float]) -> float | None:
    return round(median(values), 4) if values else None


async def collect_project_metrics(
    session: AsyncSession, project_id: str
) -> ProjectMetrics:
    """Compute project metrics in a fixed number of queries."""

    items = list(
        (
            await session.execute(
                select(WorkItem).where(WorkItem.project_id == project_id)
            )
        )
        .scalars()
        .all()
    )

    by_status: dict[str, int] = {}
    for item in items:
        by_status[item.status.value] = by_status.get(item.status.value, 0) + 1

    done = [item for item in items if item.status is WorkStatus.done]
    open_items = [
        item
        for item in items
        if item.status
        not in (WorkStatus.done, WorkStatus.cancelled, WorkStatus.archived)
    ]

    estimate_done = round(sum(item.estimate_hours or 0.0 for item in done), 4)
    estimate_open = round(sum(item.estimate_hours or 0.0 for item in open_items), 4)

    totals = (
        await session.execute(
            select(
                func.coalesce(func.sum(Metric.active_ms), 0),
                func.coalesce(func.sum(Metric.prompt_tokens), 0),
                func.coalesce(func.sum(Metric.completion_tokens), 0),
                func.coalesce(func.sum(Metric.cache_read_tokens), 0),
                func.coalesce(func.sum(Metric.cache_write_tokens), 0),
                func.coalesce(func.sum(Metric.cost_usd), 0.0),
            )
            .join(WorkSession, Metric.session_id == WorkSession.id)
            .join(WorkItem, WorkSession.work_item_id == WorkItem.id)
            .where(WorkItem.project_id == project_id)
        )
    ).one()

    active_ms, prompt_tokens, completion_tokens, cache_read, cache_write, cost = totals

    done_ids = [item.id for item in done]
    actual_done_ms = 0
    if done_ids:
        actual_done_ms = (
            await session.execute(
                select(func.coalesce(func.sum(Metric.active_ms), 0))
                .join(WorkSession, Metric.session_id == WorkSession.id)
                .where(WorkSession.work_item_id.in_(done_ids))
            )
        ).scalar_one()

    actual_hours_done = _ms_to_hours(float(actual_done_ms or 0))
    variance_ratio = (
        round(actual_hours_done / estimate_done, 4) if estimate_done > 0 else None
    )

    cycle_values = [
        _hours((item.done_at - item.started_at).total_seconds())
        for item in done
        if item.done_at is not None and item.started_at is not None
    ]
    lead_values = [
        _hours((item.done_at - item.created_at).total_seconds())
        for item in done
        if item.done_at is not None and item.created_at is not None
    ]

    return ProjectMetrics(
        total_items=len(items),
        completed_items=len(done),
        by_status=by_status,
        active_time_hours=_ms_to_hours(float(active_ms or 0)),
        estimate_hours_total=round(estimate_done + estimate_open, 4),
        estimate_hours_done=estimate_done,
        estimate_hours_open=estimate_open,
        actual_hours_done=actual_hours_done,
        variance_ratio=variance_ratio,
        median_cycle_time_hours=_median_hours(cycle_values),
        median_lead_time_hours=_median_hours(lead_values),
        prompt_tokens=int(prompt_tokens or 0),
        completion_tokens=int(completion_tokens or 0),
        cache_read_tokens=int(cache_read or 0),
        cache_write_tokens=int(cache_write or 0),
        total_tokens=int(prompt_tokens or 0) + int(completion_tokens or 0),
        cost_usd=round(float(cost or 0.0), 6),
    )
