# TaskHub

Local MCP-native single source of truth for tasks, projects, notes and metrics.
Replaces scattered file backlogs (`*/ai/tasks/`, `*/docs/backlog/`,
`shared-infrastructure/tasks/`) with one database and one workflow.

## Status (M1 + M2)

M1: database schema, Alembic migrations, FastMCP server, scope resolution by
`cwd`, tools `create_task`, `get_context`, `get_next_item`, `list_projects`.

M2: evaluation core — RICE prioritisation, EVM (CPI/SPI/EAC/ETC/VAC, Decimal),
Monte Carlo duration forecast, automatic project metrics, and MCP tools
`project_status`, `evaluate_project`, `forecast_project`.

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync --extra dev
uv run taskhub init-db          # create tables without Alembic (quick start)
uv run alembic upgrade head     # or apply migrations (recommended)
```

## Run MCP server (stdio)

```bash
uv run taskhub-mcp
```

Streamable HTTP:

```bash
TASKHUB_TRANSPORT=http TASKHUB_PORT=7801 uv run taskhub-mcp
```

## Connect from Claude Code / Cline / Cursor

`.mcp.json`:

```json
{
  "mcpServers": {
    "taskhub": {
      "command": "uv",
      "args": ["run", "--directory", "/home/maxbogus/Repositories/taskhub", "taskhub-mcp"],
      "env": {
        "TASKHUB_DB_URL": "sqlite+aiosqlite:////home/maxbogus/.taskhub/taskhub.db"
      }
    }
  }
}
```

## Architecture

```
taskhub-mcp   — MCP server (stdio / streamable HTTP :7801)
taskhub-web   — FastAPI REST + dashboard (M8)
taskhub-worker— metric ingest, backup, status snapshots (M4/M9)
```

Three processes over one database. Layout:

```
src/taskhub/
  config.py      pydantic-settings
  db.py          async engine / session
  models.py      SQLAlchemy 2.0 schema
  repo/           thin repositories over AsyncSession
  services/       scope, prioritizer, gates
  mcp/            FastMCP server, tools, resources
  api/            FastAPI REST
  cli.py          small human CLI
```

## Database

SQLite by default (`sqlite+aiosqlite:///./taskhub.db`), PostgreSQL via
`TASKHUB_DB_URL`. Migrations are managed by Alembic.

## Testing

```bash
uv run pytest
uv run ruff check
uv run mypy
```

## Design notes

- Soft archive only: nothing is deleted physically.
- Enum stored as text on SQLite (`native_enum=False`).
- JSON uses `JSONB` on PostgreSQL and `JSON` elsewhere.
- `WorkItem.due_at` is used for EVM planned value and lead time.

## Next milestones

M2 evaluation core (RICE + EVM + Monte Carlo), M3 backlog import,
M4 metric ingest, M5 gates/flow, M6 notes/docs/templates, M7 claims,
M8 dashboard, M9 deployment/backup.
