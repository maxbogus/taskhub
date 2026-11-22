"""FastAPI REST API and single-page dashboard (M8)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from taskhub.config import get_settings
from taskhub.db import dispose_engine, init_db
from taskhub.mcp import tools

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create tables on startup and dispose the engine on shutdown."""

    await init_db()
    try:
        yield
    finally:
        await dispose_engine()


app = FastAPI(title="TaskHub API", version="0.1.0", lifespan=lifespan)

#: Paths that stay reachable without a token.
#:
#: The liveness probe must stay open: a health check that needs a token
#: cannot be used by Docker or by a load balancer.
OPEN_PATHS = frozenset({"/healthz", "/docs", "/openapi.json", "/redoc"})

#: Header carrying the token.
TOKEN_HEADER = "X-TaskHub-Token"


def _presented_token(request: Request) -> str | None:
    """Read the token from the request headers.

    Both a dedicated header and the usual ``Authorization: Bearer`` are
    accepted, so a browser, a CLI and a proxy can all pass the token.

    Args:
        request: The incoming request.

    Returns:
        The token, or ``None`` when it was not presented.
    """
    direct = request.headers.get(TOKEN_HEADER)
    if direct:
        return direct.strip()
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


@app.middleware("http")
async def token_guard(request: Request, call_next: Callable[[Request], Awaitable[Any]]) -> Any:
    """Require the configured token for every endpoint but the probe.

    Without a configured token the API stays open, so a local setup
    keeps working unchanged; the guard only bites when the operator
    asked for it.
    """

    token = get_settings().token
    path = request.url.path
    if not token or path in OPEN_PATHS or path.startswith("/static"):
        return await call_next(request)

    presented = _presented_token(request)
    if presented != token:
        return JSONResponse(
            {"detail": "invalid or missing token"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await call_next(request)


async def _guard[T](awaitable: Awaitable[T]) -> T:
    """Translate domain ``ValueError`` into HTTP errors."""

    try:
        return await awaitable
    except ValueError as exc:
        message = str(exc)
        status = 404 if "not found" in message else 400
        raise HTTPException(status_code=status, detail=message) from exc


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe."""

    return {"status": "ok", "service": "taskhub"}


@app.get("/api/projects")
async def api_projects() -> list[dict[str, Any]]:
    """Projects with per-status counters."""

    return await tools.list_projects()


@app.get("/api/projects/{slug}/status")
async def api_project_status(slug: str) -> dict[str, Any]:
    """EVM, forecast and health snapshot for one project."""

    return await _guard(tools.project_status(slug))


@app.get("/api/projects/{slug}/items")
async def api_project_items(
    slug: str, status: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    """Work items for a project, optionally filtered by status."""

    return await _guard(tools.list_tasks(slug, status=status, limit=limit))


@app.get("/api/projects/{slug}/documents")
async def api_project_documents(slug: str, type: str | None = None) -> list[dict[str, Any]]:
    """Documents for a project, optionally filtered by type."""

    return await _guard(tools.list_documents(slug, type=type))


@app.get("/api/projects/{slug}/claims")
async def api_project_claims(slug: str) -> list[dict[str, Any]]:
    """Active claim leases for a project."""

    return await _guard(tools.list_claims(slug))


@app.get("/api/claims")
async def api_claims() -> list[dict[str, Any]]:
    """Active claim leases across all projects."""

    return await tools.list_claims()


@app.get("/api/tasks/{item_id}")
async def api_task(item_id: str) -> dict[str, Any]:
    """Task context: item, parent, children, blockers and recent notes."""

    return await _guard(tools.get_context(item_id))


@app.get("/api/metrics")
async def api_metrics(
    project: str | None = None,
    group_by: str = "model",
    since: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregated token/cost/time metrics."""

    return await _guard(tools.metrics_report(project_slug=project, group_by=group_by, since=since))


@app.get("/api/economics")
async def api_economics(
    project: str | None = None,
    group_by: str = "model",
    since: str | None = None,
) -> list[dict[str, Any]]:
    """Money view of the agent usage, grouped by model/agent/task/project."""

    return await _guard(
        tools.economics_report(project_slug=project, group_by=group_by, since=since)
    )


@app.get("/api/economics/summary")
async def api_economics_summary(project: str | None = None) -> dict[str, Any]:
    """Totals, human baseline, savings, ROI and the money-scale EVM."""

    if not project:
        raise HTTPException(status_code=400, detail="project is required")
    return await _guard(tools.economics_summary(project))


@app.get("/api/economics/series")
async def api_economics_series(
    project: str | None = None,
    bucket: str = "day",
    since: str | None = None,
) -> dict[str, Any]:
    """Burn-rate series of the agent spend."""

    return await _guard(tools.cost_series(project_slug=project, bucket=bucket, since=since))


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    """Serve the single-page dashboard."""

    return FileResponse(STATIC_DIR / "index.html")


def run() -> None:
    """Run the web process (``taskhub-web``)."""

    settings = get_settings()
    uvicorn.run(
        "taskhub.api.main:app",
        host=settings.host,
        port=settings.web_port,
        reload=False,
    )
