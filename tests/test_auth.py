"""Tests for the API token guard (M15).

The dashboard is served on a local port, but the same API is reachable
from the network in a Docker deployment. A shared token protects it.

Three rules matter:

* the liveness probe stays open — a health check that needs a token
  cannot be used by Docker or by a load balancer;
* without a configured token the API stays open, so a local setup keeps
  working unchanged;
* with a configured token every other endpoint demands it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from taskhub.api.main import app
from taskhub.db import dispose_engine, init_db, session_scope
from taskhub.repo.projects import create_project


async def _client(
    tmp_path: Path, token: str | None, monkeypatch: pytest.MonkeyPatch
) -> AsyncClient:
    """Build a client bound to a temporary database.

    The environment is patched, not written directly: a leaked
    ``TASKHUB_DB_URL`` would break every later test that reads the
    settings.
    """
    db_file = tmp_path / "auth.db"
    monkeypatch.setenv("TASKHUB_DB_URL", f"sqlite+aiosqlite:///{db_file}")
    if token:
        monkeypatch.setenv("TASKHUB_TOKEN", token)
    else:
        monkeypatch.delenv("TASKHUB_TOKEN", raising=False)
    await dispose_engine()
    await init_db()
    async with session_scope() as session:
        await create_project(session, slug="demo", name="Demo", root_path="/tmp/demo")
        await session.commit()
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
async def open_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A client without a configured token."""
    client = await _client(tmp_path, None, monkeypatch)
    async with client:
        yield client
    await dispose_engine()


@pytest.fixture
async def guarded_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A client with a configured token."""
    client = await _client(tmp_path, "secret-token", monkeypatch)
    async with client:
        yield client
    await dispose_engine()


async def test_healthz_stays_open(guarded_client: AsyncClient) -> None:
    """The liveness probe must never need a token."""
    response = await guarded_client.get("/healthz")

    assert response.status_code == 200


async def test_api_requires_the_token(guarded_client: AsyncClient) -> None:
    """Without the token the API is closed."""
    response = await guarded_client.get("/api/projects")

    assert response.status_code == 401


async def test_api_accepts_the_token(guarded_client: AsyncClient) -> None:
    """With the token the API answers as usual."""
    response = await guarded_client.get(
        "/api/projects", headers={"X-TaskHub-Token": "secret-token"}
    )

    assert response.status_code == 200
    assert response.json()[0]["slug"] == "demo"


async def test_api_accepts_the_bearer_header(guarded_client: AsyncClient) -> None:
    """A bearer token is the usual way to pass a token."""
    response = await guarded_client.get(
        "/api/projects", headers={"Authorization": "Bearer secret-token"}
    )

    assert response.status_code == 200


async def test_wrong_token_is_refused(guarded_client: AsyncClient) -> None:
    """A wrong token is refused."""
    response = await guarded_client.get("/api/projects", headers={"X-TaskHub-Token": "wrong"})

    assert response.status_code == 401


async def test_dashboard_requires_the_token(guarded_client: AsyncClient) -> None:
    """The dashboard page is behind the guard as well."""
    response = await guarded_client.get("/")

    assert response.status_code == 401


async def test_open_api_without_a_token(open_client: AsyncClient) -> None:
    """Without a configured token the API stays open."""
    response = await open_client.get("/api/projects")

    assert response.status_code == 200


async def test_open_dashboard_without_a_token(open_client: AsyncClient) -> None:
    """The local setup keeps working unchanged."""
    response = await open_client.get("/")

    assert response.status_code == 200
