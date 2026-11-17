# TaskHub — Project Map

> Local MCP-native task/project/metrics hub. Read this file first.

## Purpose

Single source of truth for tasks, notes, documentation, estimates and metrics
across all local projects. Agents connect over MCP; humans use REST/dashboard.

## Tech stack

- Python 3.12, uv
- FastAPI + uvicorn
- SQLAlchemy 2.0 async, aiosqlite (default) / asyncpg (prod), Alembic
- mcp SDK (FastMCP), stdio + streamable HTTP
- pydantic-settings, structlog, prometheus-client
- pytest, ruff, mypy (strict)

## Structure

```
src/taskhub/
  config.py       settings (TASKHUB_* env)
  db.py           async engine, session_scope, init_db
  models.py       Project, Instance, WorkItem, Dependency, Note,
                  Document, TaskTemplate, Agent, Claim, Session, Metric
  repo/           projects, work_items, notes, metrics, claims
  services/       scope (cwd → project), prioritizer (RICE), gates,
                  evm (Decimal), forecast (Monte Carlo), metrics, evaluation,
                  backlog_importer, backup
  mcp/            server.py (FastMCP), tools.py, resources.py
  api/            FastAPI app (/healthz)
  cli.py          `taskhub init-db`, `taskhub list-projects`
tests/            pytest (async, in-memory/file SQLite)
alembic/          async migrations
```

## Commands

```bash
uv sync --extra dev
uv run taskhub init-db
uv run taskhub-mcp                       # stdio MCP
TASKHUB_TRANSPORT=http uv run taskhub-mcp
uv run pytest
uv run ruff check
uv run mypy
```

## Key conventions

- Soft archive only (`status=archived` + `archived_at`), never physical delete.
- `Enum(..., native_enum=False)` for SQLite compatibility.
- `JSON().with_variant(JSONB(), "postgresql")` for JSON columns.
- `WorkItem.due_at` drives EVM planned value and lead time.
- Claim = TTL lease; expired claims do not block work.
- Compact responses by default; full texts via MCP resources.

## Milestones

M1 scaffold (done) → M2 evaluation core (done) → M3 import (done) → M4 metrics →
M5 gates → M6 notes/docs/templates → M7 claims → M8 dashboard → M9 deploy/backup.
