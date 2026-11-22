"""Agent economics: what the agents cost, and what a human would cost (M11).

This service turns recorded usage (tokens, failures, active time) into a
money view of the work:

* **token cost** — recomputed from the model price list, so the estimate
  exists even when the vendor did not report a cost;
* **error cost** — a penalty per failed run;
* **supervision cost** — human time spent watching the agents, currently a
  flat project-level value because per-task supervision is not measured;
* **human baseline** — what the same work would have cost at the human
  hourly rate, which is what makes savings and ROI meaningful.

Two honesty rules run through the whole module:

* an unpriced model yields **no** cost and is listed in
  ``unpriced_models`` — pricing it at zero would silently understate the
  cost of the work;
* the reported ``Metric.cost_usd`` is never overwritten; it is reported
  next to the estimate so the two can be compared.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.config import get_settings
from taskhub.models import Agent, Metric, ModelPrice, Project, WorkItem, WorkStatus
from taskhub.models import Session as WorkSession
from taskhub.prices import PriceSpec
from taskhub.repo.projects import get_project_by_slug
from taskhub.services.cost_estimator import (
    error_cost_usd,
    estimate_token_cost_micro,
    human_equivalent_cost_usd,
    match_price,
    micro_usd_to_usd,
    usage_from_metric,
)
from taskhub.services.evm import EVMCalculator

#: Grouping keys accepted by :func:`economics_report`.
GROUP_BY_KEYS = ("model", "agent", "task", "project")

#: Buckets accepted by :func:`cost_series`.
BUCKETS = ("day", "week", "month")

#: USD precision kept in returned amounts.
_USD = Decimal("0.000001")


@dataclass(frozen=True)
class Rates:
    """Money rates in force for one project."""

    human_rate_usd_per_hour: Decimal
    error_cost_usd: Decimal
    supervision_cost_usd: Decimal
    source: str

    def to_dict(self) -> dict[str, Any]:
        """Return the rates as a plain dictionary."""
        return {
            "human_rate_usd_per_hour": float(self.human_rate_usd_per_hour),
            "error_cost_usd": float(self.error_cost_usd),
            "supervision_cost_usd": float(self.supervision_cost_usd),
            "source": self.source,
        }


def rub_per_usd() -> Decimal | None:
    """The configured display rate, or ``None`` when it is not set.

    USD stays the canonical currency; rubles are a **display** view that
    only appears when a rate is configured.

    Returns:
        The rate, or ``None``.
    """
    raw = get_settings().rub_per_usd
    if raw is None or raw <= 0:
        return None
    return Decimal(str(raw))


def with_rub(payload: dict[str, Any], rate: Decimal | None) -> dict[str, Any]:
    """Add a ruble twin of every USD field in a payload.

    Args:
        payload: The dictionary with ``*_usd`` fields.
        rate: The rate to use; ``None`` leaves the payload untouched.

    Returns:
        The payload with the ruble fields added.
    """
    payload["rub_per_usd"] = float(rate) if rate is not None else None
    if rate is None:
        return payload
    for key, value in list(payload.items()):
        if not key.endswith("_usd") or not isinstance(value, (int, float)):
            continue
        payload[f"{key[:-4]}_rub"] = round(float(value) * float(rate), 6)
    return payload


def _decimal(value: Any, default: str = "0") -> Decimal:
    """Read a value as Decimal without importing binary float error."""
    if value is None:
        return Decimal(default)
    return Decimal(str(value))


def resolve_rates(project: Project | None) -> Rates:
    """Resolve the money rates for a project.

    Project configuration (``Project.config["economics"]``) wins over the
    global settings, which is the same precedence used for ``flow`` and
    ``forecast`` overrides.

    Args:
        project: The project, or ``None`` for global defaults.

    Returns:
        The rates in force, with the source of the human rate.
    """
    settings = get_settings()
    human_rate = Decimal(str(settings.human_rate_usd_per_hour))
    error_rate = Decimal(str(settings.error_cost_usd))
    supervision = Decimal(str(settings.supervision_cost_usd))
    source = "settings"

    if project is not None and project.config:
        raw = project.config.get("economics")
        if isinstance(raw, dict):
            if raw.get("human_rate_usd_per_hour") is not None:
                human_rate = _decimal(raw["human_rate_usd_per_hour"])
                source = "project"
            if raw.get("error_cost_usd") is not None:
                error_rate = _decimal(raw["error_cost_usd"])
            if raw.get("supervision_cost_usd") is not None:
                supervision = _decimal(raw["supervision_cost_usd"])

    return Rates(
        human_rate_usd_per_hour=human_rate,
        error_cost_usd=error_rate,
        supervision_cost_usd=supervision,
        source=source,
    )


async def load_price_specs(
    session: AsyncSession,
    *,
    include_yaml: bool = True,
) -> list[PriceSpec]:
    """Collect the prices that apply, database first.

    The database holds prices added at runtime; ``prices.yaml`` holds the
    shipped defaults. A database row always wins over a YAML row for the
    same pattern, because it is the newer, deliberate value.

    Args:
        session: Database session.
        include_yaml: Whether to fall back to the bundled price list.

    Returns:
        The known price specs.
    """
    rows = list((await session.execute(select(ModelPrice))).scalars().all())
    specs = [row.to_spec() for row in rows]

    if include_yaml:
        from taskhub.prices import load_prices

        try:
            yaml_specs = load_prices().to_specs()
        except (FileNotFoundError, ValueError):
            yaml_specs = []
        db_patterns = {spec.pattern for spec in specs}
        specs.extend(spec for spec in yaml_specs if spec.pattern not in db_patterns)

    return specs


async def _fetch_rows(
    session: AsyncSession,
    *,
    project_slug: str | None,
    since: datetime | None,
) -> list[tuple[Metric, WorkSession, WorkItem | None, Project | None, Agent | None]]:
    """Load the metrics of one project (or all) with their context rows."""
    stmt = (
        select(Metric, WorkSession, WorkItem, Project, Agent)
        .join(WorkSession, Metric.session_id == WorkSession.id)
        .outerjoin(WorkItem, WorkSession.work_item_id == WorkItem.id)
        .outerjoin(Project, WorkSession.project_id == Project.id)
        .outerjoin(Agent, WorkSession.agent_id == Agent.id)
    )
    if project_slug:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found")
        stmt = stmt.where(
            (WorkSession.project_id == project.id) | (WorkItem.project_id == project.id)
        )
    if since is not None:
        stmt = stmt.where(Metric.recorded_at >= since)

    result = await session.execute(stmt)
    return [
        (
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
        )
        for row in result.all()
    ]


def _group_key(
    group_by: str,
    metric: Metric,
    work_session: WorkSession,
    work_item: WorkItem | None,
    project: Project | None,
    agent: Agent | None,
) -> str:
    """Return the grouping key of one metric row."""
    if group_by == "model":
        return metric.model or "unknown"
    if group_by == "agent":
        return agent.name if agent is not None else "unknown"
    if group_by == "task":
        return work_item.title if work_item is not None else "unassigned"
    return project.slug if project is not None else "unassigned"


def _new_group(key: str) -> dict[str, Any]:
    """Return an empty accumulator for one group."""
    return {
        "key": key,
        "sessions": set(),
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
        "active_ms": 0,
        "duration_ms": 0,
        "reported_cost_usd": 0.0,
        "token_cost_usd": Decimal("0"),
        "priced": True,
        "failures": 0,
        "metrics": 0,
        "unpriced_tokens": 0,
    }


async def economics_report(
    session: AsyncSession,
    *,
    project_slug: str | None = None,
    group_by: str = "model",
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Aggregate the money view of agent usage.

    Args:
        session: Database session.
        project_slug: Restrict to one project; ``None`` covers all.
        group_by: One of ``model``, ``agent``, ``task``, ``project``.
        since: Only count metrics recorded at or after this moment.

    Returns:
        One row per group, sorted by total cost (descending).

    Raises:
        ValueError: The project or the grouping key is unknown.
    """
    if group_by not in GROUP_BY_KEYS:
        raise ValueError(f"unknown group_by: {group_by}; use one of {GROUP_BY_KEYS}")

    project = await get_project_by_slug(session, project_slug) if project_slug else None
    if project_slug and project is None:
        raise ValueError(f"project '{project_slug}' not found")

    rates = resolve_rates(project)
    prices = await load_price_specs(session)
    rows = await _fetch_rows(session, project_slug=project_slug, since=since)

    groups: dict[str, dict[str, Any]] = {}
    for metric, work_session, work_item, row_project, agent in rows:
        key = _group_key(group_by, metric, work_session, work_item, row_project, agent)
        entry = groups.setdefault(key, _new_group(key))
        usage = usage_from_metric(metric)

        entry["sessions"].add(work_session.id)
        entry["metrics"] += 1
        entry["prompt_tokens"] += usage.prompt_tokens
        entry["completion_tokens"] += usage.completion_tokens
        entry["cache_read_tokens"] += usage.cache_read_tokens
        entry["cache_write_tokens"] += usage.cache_write_tokens
        entry["total_tokens"] += usage.total_tokens
        entry["active_ms"] += int(metric.active_ms or 0)
        entry["duration_ms"] += int(metric.duration_ms or 0)
        entry["reported_cost_usd"] += float(metric.cost_usd or 0.0)
        if not metric.success:
            entry["failures"] += 1

        price = match_price(metric.model, prices, metric.recorded_at)
        if price is None:
            entry["priced"] = False
            entry["unpriced_tokens"] += usage.total_tokens
            continue
        entry["token_cost_usd"] += micro_usd_to_usd(estimate_token_cost_micro(usage, price))

    rate = rub_per_usd()
    result: list[dict[str, Any]] = []
    for entry in groups.values():
        failures = int(entry["failures"])
        metrics = int(entry["metrics"])
        error_cost = error_cost_usd(failures, float(rates.error_cost_usd))
        token_cost = entry["token_cost_usd"]
        total_cost = token_cost + error_cost
        active_hours = float(entry["active_ms"]) / 3_600_000.0
        priced = bool(entry["priced"])

        result.append(
            with_rub(
                {
                    "key": entry["key"],
                    "sessions": len(entry["sessions"]),
                    "metrics": metrics,
                    "prompt_tokens": entry["prompt_tokens"],
                    "completion_tokens": entry["completion_tokens"],
                    "cache_read_tokens": entry["cache_read_tokens"],
                    "cache_write_tokens": entry["cache_write_tokens"],
                    "total_tokens": entry["total_tokens"],
                    "active_hours": round(active_hours, 4),
                    "reported_cost_usd": round(float(entry["reported_cost_usd"]), 6),
                    "token_cost_usd": round(float(token_cost), 6) if priced else None,
                    "error_cost_usd": round(float(error_cost), 6),
                    "total_cost_usd": round(float(total_cost), 6),
                    "human_equivalent_cost_usd": round(
                        float(
                            human_equivalent_cost_usd(
                                active_hours, float(rates.human_rate_usd_per_hour)
                            )
                        ),
                        6,
                    ),
                    "failures": failures,
                    "error_rate": round(failures / metrics, 6) if metrics else 0.0,
                    "unpriced": not priced,
                    "unpriced_tokens": entry["unpriced_tokens"],
                },
                rate,
            )
        )

    result.sort(key=lambda row: row["total_cost_usd"], reverse=True)
    return result


async def economics_summary(
    session: AsyncSession,
    *,
    project_slug: str | None = None,
    since: datetime | None = None,
) -> dict[str, Any]:
    """Summarise the money view of a project.

    Args:
        session: Database session.
        project_slug: Restrict to one project; ``None`` covers all.
        since: Only count metrics recorded at or after this moment.

    Returns:
        Totals, the human baseline, savings, ROI and the money EVM.

    Raises:
        ValueError: The project is unknown.
    """
    project = await get_project_by_slug(session, project_slug) if project_slug else None
    if project_slug and project is None:
        raise ValueError(f"project '{project_slug}' not found")

    rates = resolve_rates(project)
    rows = await economics_report(session, project_slug=project_slug, group_by="model", since=since)

    token_cost = sum(Decimal(str(row["token_cost_usd"] or 0.0)) for row in rows)
    reported_cost = sum(Decimal(str(row["reported_cost_usd"])) for row in rows)
    error_cost = sum(Decimal(str(row["error_cost_usd"])) for row in rows)
    total_cost = token_cost + error_cost

    failures = sum(int(row["failures"]) for row in rows)
    metrics = sum(int(row["metrics"]) for row in rows)
    total_tokens = sum(int(row["total_tokens"]) for row in rows)
    unpriced_tokens = sum(int(row["unpriced_tokens"]) for row in rows)
    unpriced_models = sorted(
        str(row["key"]) for row in rows if row["unpriced"] and int(row["total_tokens"]) > 0
    )
    active_hours = sum(float(row["active_hours"]) for row in rows)

    estimate_hours = 0.0
    completed_items = 0
    done_estimate_hours = 0.0
    open_estimate_hours = 0.0
    if project is not None:
        items = list(
            (await session.execute(select(WorkItem).where(WorkItem.project_id == project.id)))
            .scalars()
            .all()
        )
        estimate_hours = sum(float(item.estimate_hours or 0.0) for item in items)
        completed_items = sum(1 for item in items if item.status is WorkStatus.done)
        done_estimate_hours = sum(
            float(item.estimate_hours or 0.0) for item in items if item.status is WorkStatus.done
        )
        open_estimate_hours = sum(
            float(item.estimate_hours or 0.0)
            for item in items
            if item.status not in (WorkStatus.done, WorkStatus.cancelled, WorkStatus.archived)
        )

    baseline = human_equivalent_cost_usd(estimate_hours, float(rates.human_rate_usd_per_hour))
    savings = baseline - total_cost
    roi = (savings / total_cost) if total_cost > 0 else None

    summary: dict[str, Any] = {
        "project": project.slug if project is not None else None,
        "currency": "USD",
        "human_rate_usd_per_hour": float(rates.human_rate_usd_per_hour),
        "rates_source": rates.source,
        "token_cost_usd": round(float(token_cost), 6),
        "error_cost_usd": round(float(error_cost), 6),
        "supervision_cost_usd": round(float(rates.supervision_cost_usd), 6),
        "total_cost_usd": round(float(total_cost), 6),
        "reported_cost_usd": round(float(reported_cost), 6),
        "human_equivalent_cost_usd": round(
            float(human_equivalent_cost_usd(active_hours, float(rates.human_rate_usd_per_hour))),
            6,
        ),
        "baseline_human_cost_usd": round(float(baseline), 6),
        "savings_usd": round(float(savings), 6),
        "roi": round(float(roi), 6) if roi is not None else None,
        "active_hours": round(active_hours, 4),
        "estimate_hours": round(estimate_hours, 4),
        "completed_items": completed_items,
        "failures": failures,
        "error_rate": round(failures / metrics, 6) if metrics else 0.0,
        "total_tokens": total_tokens,
        "unpriced_models": unpriced_models,
        "unpriced_token_share": (round(unpriced_tokens / total_tokens, 6) if total_tokens else 0.0),
    }

    summary = with_rub(summary, rub_per_usd())

    if project is not None:
        summary["progress_money"] = _money_progress(
            rates=rates,
            earned_value_hours=done_estimate_hours,
            budget_at_completion_hours=open_estimate_hours,
            planned_value_hours=0.0,
            actual_cost_usd=total_cost + rates.supervision_cost_usd,
        )

    return summary


def _money_progress(
    *,
    rates: Rates,
    earned_value_hours: float,
    budget_at_completion_hours: float,
    planned_value_hours: float,
    actual_cost_usd: Decimal,
) -> dict[str, Any]:
    """Build the money EVM from hours and the real spend.

    Earned value, budget and planned value are measured on the human
    baseline; actual cost is the **real** money spent on the agents.
    Mixing the two scales would make the index meaningless.
    """
    rate = float(rates.human_rate_usd_per_hour)
    return money_evm(
        earned_value_usd=human_equivalent_cost_usd(earned_value_hours, rate),
        actual_cost_usd=actual_cost_usd,
        budget_at_completion_usd=human_equivalent_cost_usd(budget_at_completion_hours, rate),
        planned_value_usd=human_equivalent_cost_usd(planned_value_hours, rate),
    )


async def money_progress(
    session: AsyncSession,
    project: Project,
    *,
    earned_value_hours: float,
    budget_at_completion_hours: float,
    planned_value_hours: float = 0.0,
) -> dict[str, Any]:
    """Money-scale EVM of a project, with the real agent spend as AC.

    Args:
        session: Database session.
        project: The project to evaluate.
        earned_value_hours: Estimated hours of the delivered work.
        budget_at_completion_hours: Estimated hours of the remaining work.
        planned_value_hours: Estimated hours planned for the current period.

    Returns:
        The money-scale EVM snapshot.
    """
    rates = resolve_rates(project)
    rows = await economics_report(session, project_slug=project.slug, group_by="model")
    token_cost = sum(Decimal(str(row["token_cost_usd"] or 0.0)) for row in rows)
    error_cost = sum(Decimal(str(row["error_cost_usd"])) for row in rows)
    actual_cost = token_cost + error_cost + rates.supervision_cost_usd
    return _money_progress(
        rates=rates,
        earned_value_hours=earned_value_hours,
        budget_at_completion_hours=budget_at_completion_hours,
        planned_value_hours=planned_value_hours,
        actual_cost_usd=actual_cost,
    )


def money_evm(
    *,
    earned_value_usd: Decimal,
    actual_cost_usd: Decimal,
    budget_at_completion_usd: Decimal,
    planned_value_usd: Decimal | None = None,
) -> dict[str, Any]:
    """Compute EVM on the money scale.

    This is **not** the classic CPI: earned value is measured on the human
    baseline (what the delivered work is worth) while actual cost is the
    real agent spend. The index therefore answers the build-vs-buy
    question — "how much value do we get per dollar spent" — and not
    "are we inside the budget". That is why it lives next to, and not
    instead of, the hourly EVM in ``services/evaluation.py``.

    Args:
        earned_value_usd: Human value of the delivered work.
        actual_cost_usd: Real money spent.
        budget_at_completion_usd: Human value of the remaining work.
        planned_value_usd: Human value planned for the current period.

    Returns:
        The money-scale EVM snapshot.
    """
    evm = EVMCalculator().evaluate(
        earned_value_usd,
        actual_cost_usd,
        budget_at_completion_usd,
        planned_value_usd,
    )
    return {
        "earned_value_usd": round(float(earned_value_usd), 6),
        "actual_cost_usd": round(float(actual_cost_usd), 6),
        "budget_at_completion_usd": round(float(budget_at_completion_usd), 6),
        "planned_value_usd": (
            round(float(planned_value_usd), 6) if planned_value_usd is not None else None
        ),
        "cpi": float(evm.cpi) if evm.cpi is not None else None,
        "spi": float(evm.spi) if evm.spi is not None else None,
        "eac_usd": round(float(evm.eac), 6),
        "etc_usd": round(float(evm.etc), 6),
        "vac_usd": round(float(evm.vac), 6),
        "note": (
            "EV is the human baseline, AC is the real agent spend: the index "
            "compares build-vs-buy, it is not a budget control."
        ),
    }


async def cost_series(
    session: AsyncSession,
    *,
    project_slug: str | None = None,
    bucket: str = "day",
    since: datetime | None = None,
) -> dict[str, Any]:
    """Build the burn-rate series of agent spend.

    Args:
        session: Database session.
        project_slug: Restrict to one project; ``None`` covers all.
        bucket: One of ``day``, ``week``, ``month``.
        since: Only count metrics recorded at or after this moment.

    Returns:
        The points per bucket, the cumulative spend and the burn rate.

    Raises:
        ValueError: The bucket or the project is unknown.
    """
    if bucket not in BUCKETS:
        raise ValueError(f"unknown bucket: {bucket}; use one of {BUCKETS}")

    project = await get_project_by_slug(session, project_slug) if project_slug else None
    if project_slug and project is None:
        raise ValueError(f"project '{project_slug}' not found")

    rates = resolve_rates(project)
    prices = await load_price_specs(session)
    rows = await _fetch_rows(session, project_slug=project_slug, since=since)

    buckets: dict[str, dict[str, Any]] = {}
    for metric, work_session, _item, _project, _agent in rows:
        label = _bucket_key(metric.recorded_at, bucket)
        entry = buckets.setdefault(
            label,
            {
                "bucket": label,
                "sessions": set(),
                "token_cost_usd": Decimal("0"),
                "active_ms": 0,
                "failures": 0,
                "metrics": 0,
            },
        )
        entry["sessions"].add(work_session.id)
        entry["metrics"] += 1
        entry["active_ms"] += int(metric.active_ms or 0)
        if not metric.success:
            entry["failures"] += 1

        price = match_price(metric.model, prices, metric.recorded_at)
        if price is None:
            continue
        entry["token_cost_usd"] += micro_usd_to_usd(
            estimate_token_cost_micro(usage_from_metric(metric), price)
        )

    rate = rub_per_usd()
    points: list[dict[str, Any]] = []
    cumulative = Decimal("0")
    for label in sorted(buckets):
        entry = buckets[label]
        error_cost = error_cost_usd(int(entry["failures"]), float(rates.error_cost_usd))
        total = entry["token_cost_usd"] + error_cost
        cumulative += total
        points.append(
            with_rub(
                {
                    "bucket": label,
                    "sessions": len(entry["sessions"]),
                    "metrics": entry["metrics"],
                    "token_cost_usd": round(float(entry["token_cost_usd"]), 6),
                    "error_cost_usd": round(float(error_cost), 6),
                    "total_cost_usd": round(float(total), 6),
                    "active_hours": round(float(entry["active_ms"]) / 3_600_000.0, 4),
                    "cumulative_usd": round(float(cumulative), 6),
                },
                rate,
            )
        )

    window_days = _window_days(points, bucket)
    total_spend = sum(Decimal(str(point["total_cost_usd"])) for point in points)
    burn_per_day = total_spend / Decimal(window_days) if window_days > 0 else Decimal("0")

    return with_rub(
        {
            "project": project.slug if project is not None else None,
            "bucket": bucket,
            "points": points,
            "window_days": window_days,
            "total_cost_usd": round(float(total_spend), 6),
            "burn_usd_per_day": round(float(burn_per_day), 6),
            "burn_usd_per_week": round(float(burn_per_day * 7), 6),
        },
        rate,
    )


def _window_days(points: list[dict[str, Any]], bucket: str) -> int:
    """Number of days the series spans (at least one)."""
    if not points:
        return 0
    if bucket == "day":
        return len(points)
    per_bucket = {"week": 7, "month": 30}[bucket]
    return max(1, len(points) * per_bucket)


def _bucket_key(moment: datetime, bucket: str) -> str:
    """Return the bucket label of a timestamp.

    Bucketing happens in Python rather than in SQL so the result is the
    same on SQLite and PostgreSQL.
    """
    if bucket == "day":
        return moment.date().isoformat()
    if bucket == "week":
        iso = moment.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    return moment.strftime("%Y-%m")
