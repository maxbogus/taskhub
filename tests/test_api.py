"""Tests for the REST API and dashboard (M8)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from taskhub.api.main import app
from taskhub.db import dispose_engine, init_db, session_scope
from taskhub.models import DocType, WorkStatus
from taskhub.repo.projects import create_project
from taskhub.repo.work_items import create_item
from taskhub.services.documents import upsert_document


@pytest_asyncio.fixture
async def client(monkeypatch, tmp_path: Path) -> AsyncIterator[AsyncClient]:
    db_file = tmp_path / "api.db"
    monkeypatch.setenv("TASKHUB_DB_URL", f"sqlite+aiosqlite:///{db_file}")
    await dispose_engine()
    await init_db()
    async with session_scope() as session:
        project = await create_project(session, slug="demo", name="Demo", root_path="/tmp/demo")
        await create_item(session, project_id=project.id, title="First task")
        await create_item(
            session,
            project_id=project.id,
            title="Second task",
            status=WorkStatus.ready,
        )
        await upsert_document(
            session,
            project_id=project.id,
            doc_type=DocType.spec,
            slug="arch",
            title="Architecture",
            content="content",
        )
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await dispose_engine()


async def test_healthz(client: AsyncClient) -> None:
    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_dashboard_served(client: AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "TaskHub" in response.text


async def test_projects_and_items(client: AsyncClient) -> None:
    projects = await client.get("/api/projects")
    assert projects.status_code == 200
    assert [project["slug"] for project in projects.json()] == ["demo"]

    items = await client.get("/api/projects/demo/items")
    assert items.status_code == 200
    assert len(items.json()) == 2


async def test_items_filtered_by_status(client: AsyncClient) -> None:
    ready = await client.get("/api/projects/demo/items?status=ready")

    assert ready.status_code == 200
    assert [item["title"] for item in ready.json()] == ["Second task"]


async def test_unknown_project_returns_404(client: AsyncClient) -> None:
    response = await client.get("/api/projects/missing/status")

    assert response.status_code == 404


async def test_documents_and_claims(client: AsyncClient) -> None:
    docs = await client.get("/api/projects/demo/documents?type=spec")
    assert docs.status_code == 200
    assert docs.json()[0]["slug"] == "arch"

    claims = await client.get("/api/projects/demo/claims")
    assert claims.status_code == 200
    assert claims.json() == []


async def test_task_context(client: AsyncClient) -> None:
    items = (await client.get("/api/projects/demo/items")).json()
    item_id = items[0]["id"]

    response = await client.get(f"/api/tasks/{item_id}")

    assert response.status_code == 200
    assert response.json()["item"]["id"] == item_id


async def test_metrics_endpoint(client: AsyncClient) -> None:
    response = await client.get("/api/metrics?group_by=model")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


async def test_economics_endpoints(client: AsyncClient) -> None:
    """The economics endpoints expose the money view of a project."""
    report = await client.get("/api/economics?project=demo&group_by=model")
    assert report.status_code == 200
    assert isinstance(report.json(), list)

    summary = await client.get("/api/economics/summary?project=demo")
    assert summary.status_code == 200
    body = summary.json()
    assert body["project"] == "demo"
    assert body["human_rate_usd_per_hour"] > 0

    series = await client.get("/api/economics/series?project=demo&bucket=day")
    assert series.status_code == 200
    assert series.json()["bucket"] == "day"


async def test_economics_summary_for_unknown_project(client: AsyncClient) -> None:
    """An unknown project is a 404, not a 500."""
    response = await client.get("/api/economics/summary?project=missing")

    assert response.status_code == 404


async def test_economics_series_rejects_unknown_bucket(client: AsyncClient) -> None:
    """An unsupported bucket is a 400."""
    response = await client.get("/api/economics/series?project=demo&bucket=fortnight")

    assert response.status_code == 400


async def test_project_status_includes_money_progress(client: AsyncClient) -> None:
    """The project snapshot carries the money-scale EVM."""
    response = await client.get("/api/projects/demo/status")

    assert response.status_code == 200
    assert "progress_money" in response.json()
