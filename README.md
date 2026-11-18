# TaskHub

Local MCP-native single source of truth for tasks, projects, notes and metrics.
Replaces scattered file backlogs (`*/ai/tasks/`, `*/docs/backlog/`,
`shared-infrastructure/tasks/`) with one database and one workflow.

## Status (M1–M10)

M1: database schema, Alembic migrations, FastMCP server, scope resolution by
`cwd`, tools `create_task`, `get_context`, `get_next_item`, `list_projects`.

M2: evaluation core — RICE prioritisation, EVM (CPI/SPI/EAC/ETC/VAC, Decimal),
Monte Carlo duration forecast, automatic project metrics, and MCP tools
`project_status`, `evaluate_project`, `forecast_project`.

M3: idempotent backlog import from `*/ai/tasks/`, `*/docs/backlog/`,
`*/backlog/` and `shared-infrastructure/tasks/BACKLOG.md`; SQLite online
backup before import; MCP tools `import_backlog` and `backup_db`.

M4: Cline task-history ingestion (model, tokens, cost, active time), manual
`log_activity`, per-task and per-project aggregation with `task_metrics` and
`metrics_report`; `Session.project_id` links metric-only sessions to projects.

M5: unified role-aware workflow with gates — `change_status`, `get_workflow`,
`update_task`; audit notes on every transition; project-level flow overrides.

M6: notes, documents and templates — `add_note`, `search_notes`,
`upsert_document`/`get_document`/`list_documents`, `import_templates`,
`apply_template`; MCP resources `project://{slug}/specs` and
`project://{slug}/notes`; markdown template import with YAML frontmatter mapped
to RICE, estimates and acceptance criteria.

M7: TTL claim leases — `register_agent`, `claim_task`/`renew_claim`/`release_task`,
`list_claims`, `list_tasks`; resource `project://{slug}/claims`; expired leases
never block work.

M8: FastAPI REST API (`/api/projects`, `/api/projects/{slug}/status|items|documents|claims`,
`/api/tasks/{id}`, `/api/metrics`) and a single-page dashboard served at `/`;
`taskhub-web` entry point.

M9: Docker deployment (multi-stage, non-root, healthchecks) and backup retention —
`taskhub backup --keep N`, `backup_keep` setting, `backup_db(keep=...)`.

M10: ledger/notes collection and post export — `services/note_importer.py`
imports `docs/ledger/*.jsonl`, `docs/HANDOFF.md`, `docs/notes/`,
`docs/sessions/`, `.clinerules/*-ledger*.md` and `~/.cline/memory/*.jsonl`
(pseudo-project `memory`); notes are linked to tasks by `tickets` ids and
upserted idempotently. `services/export.py` renders a markdown/JSON digest for
writing posts (`taskhub export`, MCP `export_notes`). Per-folder source map in
`docs/COLLECTION_MAP.md`.

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync --extra dev
uv run taskhub init-db          # create tables without Alembic (quick start)
uv run alembic upgrade head     # or apply migrations (recommended)
```

## Collect tasks from all projects

```bash
# 1. register every directory under ~/Repositories that has task sources
uv run taskhub register-projects --root ~/Repositories --max-depth 2

# 2. preview the import (no writes)
uv run taskhub import-all --dry-run

# 3. import for real (one SQLite backup is taken first)
uv run taskhub import-all

# re-import a subset
uv run taskhub import-all --project ai-gateway --project books
```

The importer recursively scans `ai/tasks/**`, `ai/backlog/**`, `docs/backlog/**`,
`docs/tasks/**`, `backlog/**` and `tasks/**` (template folders excluded) and
upserts by `(project, external_ref)`, so re-runs update instead of duplicating.
MCP exposes the same flow via `register_projects` and `import_all_backlogs`.

## Collect ledgers and notes

```bash
uv run taskhub import-notes              # project ledgers + ~/.cline/memory
uv run taskhub import-notes --no-memory  # project ledgers only
uv run taskhub import-all --notes        # tasks and notes in one run
```

Sources: `docs/ledger/*.jsonl`, `docs/HANDOFF.md`, `docs/notes/`,
`docs/sessions/`, `reports/sessions/`, `.clinerules/*-ledger*.md` and the
global `~/.cline/memory/*.jsonl` (pseudo-project `memory`). Notes are
schema-agnostic, linked to tasks by `tickets` ids / task-id tokens, and
upserted by `(project_id, external_ref)`. See `docs/COLLECTION_MAP.md` for the
per-folder map.

## Export a digest for posts

```bash
uv run taskhub export --project chem_trial --format markdown --out posts/chem.md
uv run taskhub export --kind insight --since 2026-10-01 --format json --limit 200
```

The digest groups notes by project and includes the linked task title/status, so
it can be fed directly to the text skills to write blog or social posts.

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

## Web API and dashboard

```bash
uv run taskhub-web          # FastAPI + dashboard on http://127.0.0.1:8000
```

| Endpoint | Description |
|---|---|
| `GET /healthz` | Liveness probe |
| `GET /api/projects` | Projects with per-status counters |
| `GET /api/projects/{slug}/status` | EVM, forecast and health |
| `GET /api/projects/{slug}/items?status=` | Work items (optionally filtered) |
| `GET /api/projects/{slug}/documents?type=` | Documents (optionally filtered) |
| `GET /api/projects/{slug}/claims` | Active claim leases |
| `GET /api/claims` | Active claims across projects |
| `GET /api/tasks/{id}` | Task context |
| `GET /api/metrics?project=&group_by=` | Aggregated metrics |
| `GET /` | Single-page dashboard |

## Architecture

```
taskhub-mcp   — MCP server (stdio / streamable HTTP :7801)
taskhub-web   — FastAPI REST + dashboard (:8000)
taskhub       — CLI: init-db, list-projects, import-templates, backup
```

Processes over one database. Layout:

```
src/taskhub/
  config.py      pydantic-settings
  db.py          async engine / session
  models.py      SQLAlchemy 2.0 schema
  repo/           thin repositories over AsyncSession
  services/       scope, prioritizer, gates, claims, documents, templates,
                  evm, forecast, metrics, evaluation, backlog_importer,
                  backup, metric_ingest
  mcp/            FastMCP server, tools, resources
  api/            FastAPI REST + dashboard (static/index.html)
  cli.py          small human CLI
```

## Database

SQLite by default (`sqlite+aiosqlite:///./taskhub.db`), PostgreSQL via
`TASKHUB_DB_URL`. Migrations are managed by Alembic.

## Deployment (Docker)

```bash
docker compose up -d --build
# dashboard: http://127.0.0.1:8000
# MCP (streamable HTTP): http://127.0.0.1:7801
```

Two services share one SQLite database on the `taskhub-data` volume; backups go
to the `taskhub-backups` volume. The image is multi-stage and runs as a non-root
`app` user. For production, point `TASKHUB_DB_URL` at PostgreSQL (asyncpg is
already a dependency).

## Backup

```bash
uv run taskhub backup --keep 7      # online VACUUM INTO backup + retention
```

`backup_keep` (default 7) controls retention; `0` keeps every backup. The MCP
tool `backup_db(keep=...)` exposes the same behaviour.

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

## Milestones

All planned milestones M1–M9 are complete. Suggested follow-ups: SQLite FTS5 for
note search, partitioned metrics for PostgreSQL, and auth in front of the dashboard.
