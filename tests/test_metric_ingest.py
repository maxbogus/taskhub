"""Tests for Cline metric ingestion."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Metric, SessionSource
from taskhub.models import Session as WorkSession
from taskhub.repo.projects import create_project
from taskhub.services.metric_ingest import _home_aliases, analyze_task, ingest_cline_tasks


def test_home_aliases_follow_environment(monkeypatch) -> None:
    monkeypatch.setenv("CLINE_HOME_ALIASES", "custom-user, another-user")

    aliases = _home_aliases()

    assert Path.home().name in aliases
    assert {"custom-user", "another-user"} <= aliases


def _write_task(
    root: Path,
    task_id: str,
    *,
    repo: str = "candidate-tracker",
    model: str = "MiniMax-M2.7",
    prompt: int = 1000,
    completion: int = 100,
    cost: float = 0.01,
) -> Path:
    folder = root / task_id
    folder.mkdir(parents=True)
    metadata = {"files_in_context": [{"path": f"/home/user/Repositories/{repo}/src/app.py"}]}
    (folder / "task_metadata.json").write_text(json.dumps(metadata))
    conversation = [
        {"role": "user", "ts": 1_000_000_000_000, "content": "hi"},
        {
            "role": "assistant",
            "ts": 1_000_000_060_000,
            "content": "a",
            "modelInfo": {"modelId": model, "mode": "act"},
            "metrics": {
                "tokens": {"prompt": prompt, "completion": completion, "cached": 10},
                "cost": cost,
            },
        },
        {
            "role": "assistant",
            "ts": 1_000_000_120_000,
            "content": "b",
            "modelInfo": {"modelId": model, "mode": "act"},
            "metrics": {
                "tokens": {"prompt": prompt, "completion": completion, "cached": 10},
                "cost": cost,
            },
        },
    ]
    (folder / "api_conversation_history.json").write_text(json.dumps(conversation))
    return folder


def test_analyze_task_extracts_metrics(tmp_path: Path) -> None:
    folder = _write_task(tmp_path, "1001")

    analysis = analyze_task(folder)

    assert analysis is not None
    assert analysis.task_id == "1001"
    assert analysis.repos_touched == ["candidate-tracker"]
    assert analysis.primary_model == "MiniMax-M2.7"
    usage = analysis.models["MiniMax-M2.7"]
    assert usage.prompt_tokens == 2000
    assert usage.completion_tokens == 200
    assert usage.cache_read_tokens == 20
    assert usage.cost_usd == 0.02
    assert usage.messages == 2
    assert usage.active_ms == 60_000
    assert usage.duration_ms == 60_000
    assert analysis.active_ms == 120_000
    assert analysis.message_count == 3


async def test_ingest_is_idempotent(session: AsyncSession, tmp_path: Path) -> None:
    tasks_root = tmp_path / "cline"
    _write_task(tasks_root, "1001")
    project = await create_project(
        session,
        slug="candidate-tracker",
        name="Candidate Tracker",
        root_path="/home/user/Repositories/candidate-tracker",
    )
    await session.commit()

    first = await ingest_cline_tasks(session, tasks_root=tasks_root)
    second = await ingest_cline_tasks(session, tasks_root=tasks_root)

    assert first.scanned == 1
    assert first.sessions_created == 1
    assert first.metrics_written == 1
    assert second.sessions_created == 0
    assert second.sessions_updated == 1
    assert second.metrics_written == 1

    sessions = list((await session.execute(select(WorkSession))).scalars().all())
    metrics = list((await session.execute(select(Metric))).scalars().all())
    assert len(sessions) == 1
    assert len(metrics) == 1
    assert sessions[0].source is SessionSource.cline
    assert sessions[0].project_id == project.id
    assert sessions[0].model == "MiniMax-M2.7"
    assert metrics[0].prompt_tokens == 2000
    assert metrics[0].cost_usd == 0.02


async def test_unmapped_task_is_skipped(session: AsyncSession, tmp_path: Path) -> None:
    tasks_root = tmp_path / "cline"
    _write_task(tasks_root, "1002", repo="unknown-repo")
    await create_project(
        session,
        slug="candidate-tracker",
        name="Candidate Tracker",
        root_path="/home/user/Repositories/candidate-tracker",
    )
    await session.commit()

    report = await ingest_cline_tasks(session, tasks_root=tasks_root)

    assert report.skipped == 1
    assert report.sessions_created == 0


async def test_dry_run_does_not_write(session: AsyncSession, tmp_path: Path) -> None:
    tasks_root = tmp_path / "cline"
    _write_task(tasks_root, "1003")
    await create_project(
        session,
        slug="candidate-tracker",
        name="Candidate Tracker",
        root_path="/home/user/Repositories/candidate-tracker",
    )
    await session.commit()

    report = await ingest_cline_tasks(session, tasks_root=tasks_root, dry_run=True)

    assert report.sessions_created == 1
    assert list((await session.execute(select(WorkSession))).scalars().all()) == []
