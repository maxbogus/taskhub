"""Metric ingestion from Cline task history and manual ``log_activity`` (M4).

Cline stores one folder per task under
``~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/tasks``. Each task
has ``task_metadata.json`` (files in context) and
``api_conversation_history.json`` (messages with ``ts``, ``modelInfo`` and
``metrics.tokens``/``metrics.cost``). This module ports the donor scanner and
adds model extraction and per-project sessions.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import (
    Agent,
    AgentRole,
    Metric,
    Project,
    SessionSource,
    WorkItem,
)
from taskhub.models import (
    Session as WorkSession,
)
from taskhub.repo.projects import get_project_by_slug

ACTIVE_GAP_SEC = int(os.environ.get("CLINE_ACTIVE_GAP_SEC", "300"))
DEFAULT_TASKS_ROOT = Path(
    os.environ.get(
        "CLINE_TASKS_ROOT",
        "~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/tasks",
    )
).expanduser()

SKIP_DIRS = {
    "",
    ".config",
    "Code",
    "User",
    "globalStorage",
    "saoudrizwan.claude-dev",
    "tasks",
    "Repositories",
    "projects",
    "dev",
}


def _home_aliases() -> set[str]:
    """Path components that may stand for the current user's home directory."""

    aliases = {"home", "Users", "user", "~"}
    aliases.add(Path.home().name)
    for env_var in ("USER", "USERNAME", "LOGNAME"):
        value = os.environ.get(env_var, "").strip()
        if value:
            aliases.add(value)
    extra = os.environ.get("CLINE_HOME_ALIASES", "")
    aliases.update(part.strip() for part in extra.split(",") if part.strip())
    return aliases


HOME_ALIASES = _home_aliases()


@dataclass
class ModelUsage:
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    messages: int = 0
    active_ms: int = 0
    duration_ms: int = 0


@dataclass
class TaskAnalysis:
    task_id: str
    repos_touched: list[str] = field(default_factory=list)
    subdirs_touched: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int = 0
    active_ms: int = 0
    message_count: int = 0
    models: dict[str, ModelUsage] = field(default_factory=dict)

    @property
    def primary_model(self) -> str | None:
        if not self.models:
            return None
        return max(
            self.models.values(),
            key=lambda usage: (usage.messages, usage.prompt_tokens + usage.completion_tokens),
        ).model


@dataclass
class IngestReport:
    scanned: int = 0
    sessions_created: int = 0
    sessions_updated: int = 0
    metrics_written: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    projects: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "sessions_created": self.sessions_created,
            "sessions_updated": self.sessions_updated,
            "metrics_written": self.metrics_written,
            "skipped": self.skipped,
            "errors": self.errors,
            "projects": self.projects,
        }


def safe_json_load(path: Path) -> Any | None:
    import json

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            return None
        return json.loads(text)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def ts_to_dt(ts: int | float | None) -> datetime | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts) / 1000.0, tz=UTC).replace(tzinfo=None)
    except (ValueError, OSError):
        return None


def _active_seconds(ts_list_ms: list[int]) -> float:
    """Sum of within-gap intervals; gaps above the threshold split sessions."""

    total = 0.0
    for index in range(1, len(ts_list_ms)):
        diff = (ts_list_ms[index] - ts_list_ms[index - 1]) / 1000.0
        if 0 < diff <= ACTIVE_GAP_SEC:
            total += diff
    return total


def _extract_repos_and_subdirs(
    files_in_context: list[dict[str, Any]],
) -> tuple[set[str], set[str]]:
    repos: set[str] = set()
    subdirs: set[str] = set()
    for entry in files_in_context:
        path = str(entry.get("path", ""))
        parts = path.lstrip("/").split("/")
        index = 0
        while index < len(parts) and (parts[index] in HOME_ALIASES or parts[index] in SKIP_DIRS):
            index += 1
        if index >= len(parts):
            continue
        repo = parts[index]
        repos.add(repo)
        if len(parts) > index + 1:
            subdirs.add("/".join(parts[index + 1 :]))
    return repos, subdirs


def _model_from_message(message: dict[str, Any]) -> str:
    info = message.get("modelInfo")
    if isinstance(info, dict):
        return str(info.get("modelId") or info.get("model") or "unknown")
    return str(message.get("model") or "unknown")


def analyze_task(task_folder: Path) -> TaskAnalysis | None:
    """Analyse one Cline task folder; return None when unusable."""

    metadata = safe_json_load(task_folder / "task_metadata.json")
    conversation = safe_json_load(task_folder / "api_conversation_history.json")
    if metadata is None or not isinstance(conversation, list):
        return None

    repos, subdirs = _extract_repos_and_subdirs(metadata.get("files_in_context", []))
    timestamps: list[int] = sorted(
        int(message["ts"])
        for message in conversation
        if isinstance(message.get("ts"), (int, float))
    )

    models: dict[str, ModelUsage] = {}
    per_model_ts: dict[str, list[int]] = {}
    for message in conversation:
        message_metrics = message.get("metrics")
        model_info = message.get("modelInfo")
        if not isinstance(message_metrics, dict) and not isinstance(model_info, dict):
            # user/tool messages carry no model or token metrics
            continue
        model = _model_from_message(message)
        usage = models.setdefault(model, ModelUsage(model=model))
        metrics = message_metrics if isinstance(message_metrics, dict) else {}
        tokens = metrics.get("tokens") or {}
        usage.prompt_tokens += int(tokens.get("prompt", 0) or 0)
        usage.completion_tokens += int(tokens.get("completion", 0) or 0)
        usage.cache_read_tokens += int(tokens.get("cached", 0) or 0)
        usage.cost_usd += float(metrics.get("cost", 0.0) or 0.0)
        usage.messages += 1
        ts = message.get("ts")
        if isinstance(ts, (int, float)):
            per_model_ts.setdefault(model, []).append(int(ts))

    for model, stamps in per_model_ts.items():
        ordered = sorted(stamps)
        if len(ordered) >= 2:
            usage = models[model]
            usage.duration_ms = ordered[-1] - ordered[0]
            usage.active_ms = int(_active_seconds(ordered) * 1000)

    return TaskAnalysis(
        task_id=task_folder.name,
        repos_touched=sorted(repos),
        subdirs_touched=sorted(subdirs),
        started_at=ts_to_dt(timestamps[0]) if timestamps else None,
        ended_at=ts_to_dt(timestamps[-1]) if timestamps else None,
        duration_ms=(timestamps[-1] - timestamps[0] if len(timestamps) >= 2 else 0),
        active_ms=(int(_active_seconds(timestamps) * 1000) if len(timestamps) >= 2 else 0),
        message_count=len(conversation),
        models=models,
    )


def scan_tasks(
    tasks_root: Path | str | None = None, limit: int | None = None
) -> list[TaskAnalysis]:
    """Scan Cline task folders, newest first."""

    root = Path(tasks_root).expanduser() if tasks_root is not None else DEFAULT_TASKS_ROOT
    if not root.is_dir():
        return []

    results: list[TaskAnalysis] = []
    for entry in sorted(root.iterdir(), reverse=True):
        if not entry.is_dir() or not entry.name.isdigit():
            continue
        analysis = analyze_task(entry)
        if analysis is None:
            continue
        results.append(analysis)
        if limit is not None and len(results) >= limit:
            break
    return results


async def ingest_cline_tasks(
    session: AsyncSession,
    *,
    project_slug: str | None = None,
    tasks_root: Path | str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> IngestReport:
    """Ingest Cline task metrics into sessions and metrics.

    When ``project_slug`` is omitted, tasks are mapped to projects by
    ``repos_touched`` (project slug, name or root directory name). A backup is
    not created here: ingestion only adds metric rows and is idempotent.
    """

    report = IngestReport()
    projects = list(
        (await session.execute(select(Project).where(Project.archived_at.is_(None))))
        .scalars()
        .all()
    )
    if project_slug:
        projects = [p for p in projects if p.slug == project_slug]
        if not projects:
            raise ValueError(f"project '{project_slug}' not found")

    by_name: dict[str, Project] = {}
    for candidate in projects:
        by_name[candidate.slug] = candidate
        by_name[candidate.name] = candidate
        by_name[Path(candidate.root_path).name] = candidate

    analyses = scan_tasks(tasks_root, limit=limit)
    report.scanned = len(analyses)
    now = datetime.now(UTC).replace(tzinfo=None)

    for analysis in analyses:
        project: Project | None = projects[0] if project_slug else None
        if project is None:
            for repo in analysis.repos_touched:
                project = by_name.get(repo)
                if project is not None:
                    break
        if project is None:
            report.skipped += 1
            continue
        if dry_run:
            report.sessions_created += 1
            report.projects[project.slug] = report.projects.get(project.slug, 0) + 1
            continue

        try:
            work_session = (
                await session.execute(
                    select(WorkSession).where(
                        WorkSession.source == SessionSource.cline,
                        WorkSession.external_id == analysis.task_id,
                    )
                )
            ).scalar_one_or_none()

            if work_session is None:
                work_session = WorkSession(
                    project_id=project.id,
                    source=SessionSource.cline,
                    external_id=analysis.task_id,
                    model=analysis.primary_model,
                    started_at=analysis.started_at,
                    ended_at=analysis.ended_at,
                    meta={},
                )
                session.add(work_session)
                await session.flush()
                report.sessions_created += 1
            else:
                work_session.project_id = project.id
                work_session.model = analysis.primary_model
                work_session.started_at = analysis.started_at
                work_session.ended_at = analysis.ended_at
                report.sessions_updated += 1

            meta = dict(work_session.meta or {})
            meta.update(
                {
                    "repos_touched": analysis.repos_touched,
                    "subdirs_touched": analysis.subdirs_touched,
                    "message_count": analysis.message_count,
                    "duration_ms": analysis.duration_ms,
                    "active_ms": analysis.active_ms,
                    "ingested_at": now.isoformat(),
                }
            )
            work_session.meta = meta

            for usage in analysis.models.values():
                metric = (
                    await session.execute(
                        select(Metric).where(
                            Metric.session_id == work_session.id,
                            Metric.model == usage.model,
                        )
                    )
                ).scalar_one_or_none()
                if metric is None:
                    metric = Metric(session_id=work_session.id)
                    session.add(metric)
                metric.model = usage.model
                metric.prompt_tokens = usage.prompt_tokens
                metric.completion_tokens = usage.completion_tokens
                metric.cache_read_tokens = usage.cache_read_tokens
                metric.cache_write_tokens = 0
                metric.duration_ms = usage.duration_ms
                metric.active_ms = usage.active_ms
                metric.cost_usd = usage.cost_usd if usage.cost_usd > 0 else None
                metric.success = True
                metric.raw = {"messages": usage.messages}
                report.metrics_written += 1

            report.projects[project.slug] = report.projects.get(project.slug, 0) + 1
        except Exception as exc:
            report.errors.append(f"{analysis.task_id}: {exc}")

    if not dry_run:
        await session.commit()
    return report


async def log_activity(
    session: AsyncSession,
    *,
    project_slug: str,
    work_item_id: str | None = None,
    agent_name: str = "human",
    source: str = "manual",
    external_id: str | None = None,
    model: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    duration_ms: int = 0,
    active_ms: int = 0,
    cost_usd: float | None = None,
    success: bool = True,
    raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a manual session/metric (active collection)."""

    project = await get_project_by_slug(session, project_slug)
    if project is None:
        raise ValueError(f"project '{project_slug}' not found")
    try:
        source_enum = SessionSource(source)
    except ValueError:
        source_enum = SessionSource.manual

    agent = (
        await session.execute(select(Agent).where(Agent.name == agent_name))
    ).scalar_one_or_none()
    if agent is None:
        agent = Agent(name=agent_name, kind="mcp-client", role=AgentRole.agent)
        session.add(agent)
        await session.flush()

    now = datetime.now(UTC).replace(tzinfo=None)
    work_session: WorkSession | None = None
    if external_id:
        work_session = (
            await session.execute(
                select(WorkSession).where(
                    WorkSession.source == source_enum,
                    WorkSession.external_id == external_id,
                )
            )
        ).scalar_one_or_none()
    if work_session is None:
        work_session = WorkSession(
            project_id=project.id,
            work_item_id=work_item_id,
            agent_id=agent.id,
            source=source_enum,
            external_id=external_id,
            model=model,
            started_at=now,
            ended_at=now,
            meta={},
        )
        session.add(work_session)
        await session.flush()
    else:
        if work_item_id:
            work_session.work_item_id = work_item_id
        work_session.agent_id = agent.id
        work_session.project_id = project.id
        if model:
            work_session.model = model
        work_session.ended_at = now

    metric = Metric(
        session_id=work_session.id,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        duration_ms=duration_ms,
        active_ms=active_ms,
        cost_usd=cost_usd,
        success=success,
        raw=raw or {},
    )
    session.add(metric)
    await session.flush()
    await session.commit()
    return {
        "session_id": work_session.id,
        "metric_id": metric.id,
        "project": project.slug,
        "work_item_id": work_session.work_item_id,
    }


async def task_metrics(session: AsyncSession, item_id: str) -> dict[str, Any]:
    """Aggregate metrics for one work item."""

    item = await session.get(WorkItem, item_id)
    if item is None:
        raise ValueError(f"work item '{item_id}' not found")
    rows = list(
        (
            await session.execute(
                select(Metric, WorkSession)
                .join(WorkSession, Metric.session_id == WorkSession.id)
                .where(WorkSession.work_item_id == item_id)
            )
        ).all()
    )

    totals: dict[str, float | int] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "active_ms": 0,
        "duration_ms": 0,
        "cost_usd": 0.0,
    }
    by_model: dict[str, dict[str, float | int]] = {}
    session_ids: set[str] = set()

    for metric, work_session in rows:
        session_ids.add(work_session.id)
        model = metric.model or "unknown"
        entry = by_model.setdefault(model, dict(totals))
        prompt = int(metric.prompt_tokens or 0)
        completion = int(metric.completion_tokens or 0)
        cache_read = int(metric.cache_read_tokens or 0)
        cache_write = int(metric.cache_write_tokens or 0)
        active = int(metric.active_ms or 0)
        duration = int(metric.duration_ms or 0)
        cost = float(metric.cost_usd or 0.0)
        for target in (entry, totals):
            target["prompt_tokens"] = int(target["prompt_tokens"]) + prompt
            target["completion_tokens"] = int(target["completion_tokens"]) + completion
            target["cache_read_tokens"] = int(target["cache_read_tokens"]) + cache_read
            target["cache_write_tokens"] = int(target["cache_write_tokens"]) + cache_write
            target["active_ms"] = int(target["active_ms"]) + active
            target["duration_ms"] = int(target["duration_ms"]) + duration
            target["cost_usd"] = float(target["cost_usd"]) + cost

    return {
        "item_id": item.id,
        "title": item.title,
        "sessions": len(session_ids),
        "active_hours": round(float(totals["active_ms"]) / 3_600_000.0, 4),
        **totals,
        "by_model": by_model,
    }


async def metrics_report(
    session: AsyncSession,
    *,
    project_slug: str | None = None,
    group_by: str = "model",
    since: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate metrics by model, agent, task or project."""

    rows = list(
        (
            await session.execute(
                select(Metric, WorkSession, WorkItem, Project, Agent)
                .join(WorkSession, Metric.session_id == WorkSession.id)
                .outerjoin(WorkItem, WorkSession.work_item_id == WorkItem.id)
                .outerjoin(Project, WorkSession.project_id == Project.id)
                .outerjoin(Agent, WorkSession.agent_id == Agent.id)
            )
        ).all()
    )

    if project_slug:
        project_filter = await get_project_by_slug(session, project_slug)
        if project_filter is None:
            raise ValueError(f"project '{project_slug}' not found")
        rows = [
            row
            for row in rows
            if (row[3] is not None and row[3].id == project_filter.id)
            or (row[2] is not None and row[2].project_id == project_filter.id)
        ]

    if since:
        cutoff = datetime.fromisoformat(since).replace(tzinfo=None)
        rows = [row for row in rows if row[0].recorded_at >= cutoff]

    groups: dict[str, dict[str, Any]] = {}
    for metric, work_session, work_item, project, agent in rows:
        if group_by == "model":
            key = metric.model or "unknown"
        elif group_by == "agent":
            key = agent.name if agent is not None else "unknown"
        elif group_by == "task":
            key = work_item.title if work_item is not None else "unassigned"
        elif group_by == "project":
            key = project.slug if project is not None else "unassigned"
        else:
            raise ValueError(f"unknown group_by: {group_by}")

        entry = groups.setdefault(
            key,
            {
                "key": key,
                "sessions": set(),
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "active_ms": 0,
                "duration_ms": 0,
                "cost_usd": 0.0,
            },
        )
        entry["sessions"].add(work_session.id)
        entry["prompt_tokens"] += int(metric.prompt_tokens or 0)
        entry["completion_tokens"] += int(metric.completion_tokens or 0)
        entry["cache_read_tokens"] += int(metric.cache_read_tokens or 0)
        entry["cache_write_tokens"] += int(metric.cache_write_tokens or 0)
        entry["active_ms"] += int(metric.active_ms or 0)
        entry["duration_ms"] += int(metric.duration_ms or 0)
        entry["cost_usd"] += float(metric.cost_usd or 0.0)

    result: list[dict[str, Any]] = []
    for entry in groups.values():
        entry["sessions"] = len(entry["sessions"])
        entry["active_hours"] = round(float(entry["active_ms"]) / 3_600_000.0, 4)
        result.append(entry)
    return sorted(result, key=lambda entry: entry["cost_usd"], reverse=True)
