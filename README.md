# TaskHub

Local MCP-native single source of truth for tasks, projects, notes and metrics.
Replaces scattered file backlogs (`*/ai/tasks/`, `*/docs/backlog/`,
`shared-infrastructure/tasks/`), ledgers (`*/docs/ledger/*.jsonl`) and handoffs
with one database and one workflow. Which files are collected is declared in
`sources.yaml`; the per-folder map is `docs/COLLECTION_MAP.md`.

## Status (M1–M10)

M1: database schema, Alembic migrations, FastMCP server, scope resolution by
`cwd`, tools `create_task`, `get_context`, `get_next_item`, `list_projects`.

M2: evaluation core — RICE prioritisation, EVM (CPI/SPI/EAC/ETC/VAC, Decimal),
Monte Carlo duration forecast, automatic project metrics, and MCP tools
`project_status`, `evaluate_project`, `forecast_project`.

M3: idempotent backlog import driven by `sources.yaml` (task folders, analysis
roadmaps, root `BACKLOG.md`/`docs/TASKS.md`, README Roadmap sections); SQLite
online backup before import; MCP tools `import_backlog` and `backup_db`.

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
uv run taskhub init-db          # create tables at ~/.taskhub/taskhub.db (quick start)
uv run alembic upgrade head     # or apply migrations (recommended)
```

No environment file is required — every setting has a working default (see below).

## Defaults

Zero-config: the app runs with these defaults. Override any setting with the
`TASKHUB_*` environment variable (or copy `.env.example` to `.env`).

| Setting | Env var | Default |
|---|---|---|
| Database | `TASKHUB_DB_URL` | `sqlite+aiosqlite:///$HOME/.taskhub/taskhub.db` |
| Backup dir | `TASKHUB_BACKUP_DIR` | `$HOME/.taskhub/backups` |
| Backups to keep | `TASKHUB_BACKUP_KEEP` | `7` (`0` = keep all) |
| MCP transport | `TASKHUB_TRANSPORT` | `stdio` (`http` = streamable HTTP) |
| Host | `TASKHUB_HOST` | `127.0.0.1` |
| MCP port | `TASKHUB_PORT` | `7801` |
| Web port | `TASKHUB_WEB_PORT` | `8000` |
| Projects root | `TASKHUB_REPOSITORIES_ROOT` | `$HOME/Repositories` |
| Memory root | `TASKHUB_MEMORY_ROOT` | `$HOME/.cline/memory` |
| Sources file | `TASKHUB_SOURCES_FILE` | `src/taskhub/sources.yaml` |
| Metrics sources | `TASKHUB_METRICS_SOURCES` | `cline` |
| Claim TTL (min) | `TASKHUB_DEFAULT_CLAIM_TTL_MINUTES` | `30` |
| Log level | `TASKHUB_LOG_LEVEL` | `INFO` |

## Startup scripts & scenarios

| Scenario | Command | Endpoint |
|---|---|---|
| First run | `uv run taskhub init-db` | — |
| Discover + import tasks | `uv run taskhub register-projects --root ~/Repositories` then `uv run taskhub import-all --dry-run` then `uv run taskhub import-all` | — |
| Tasks + ledgers/notes | `uv run taskhub import-all --notes` | — |
| Ledgers/notes only | `uv run taskhub import-notes` | — |
| MCP server (stdio) | `uv run taskhub-mcp` | stdio |
| MCP server (HTTP) | `TASKHUB_TRANSPORT=http uv run taskhub-mcp` | `http://127.0.0.1:7801` |
| Web API + dashboard | `uv run taskhub-web` | `http://127.0.0.1:8000` |
| Export digest | `uv run taskhub export --project <slug> --format markdown --out out.md` | — |
| Backup | `uv run taskhub backup --keep 7` | `~/.taskhub/backups` |
| Docker (web + MCP) | `docker compose up -d --build` | `:8000` (web), `:7801` (MCP) |

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

> Migration is exposed through the TaskHub MCP server (`register_projects`,
> `import_all_backlogs`, `import_notes`); the `taskhub-migrate` skill drives it.

Sources are declared in `src/taskhub/sources.yaml` (directories, explicit files,
glob patterns and a parser per source), not in Python code. The defaults cover
`ai/tasks`, `ai/backlog`, `ai/analysis`, `docs/backlog`, `docs/tasks`, `backlog`,
`tasks`, root `BACKLOG.md`/`docs/TASKS.md`, and READMEs that contain a
Roadmap/TODO/Backlog section. Items are upserted by `(project, external_ref)`, so
re-runs update instead of duplicating. MCP exposes the same flow via
`register_projects` and `import_all_backlogs`.

## Source configuration

All collection sources live in `src/taskhub/sources.yaml`; override the whole
file with `TASKHUB_SOURCES_FILE`:

```bash
TASKHUB_SOURCES_FILE=~/my-sources.yaml uv run taskhub import-all --notes
```

```yaml
defaults:
  patterns: ["**/*.md"]
  exclude: [templates, node_modules, .git, .venv, __pycache__]
  skip_files: [INDEX.md, README.md]

sources:
  - name: ai-tasks
    kind: task              # task | note
    root: project           # project | memory
    paths: [["ai", "tasks"]]
    parser: auto            # ai_task | frontmatter | shared_infra |
                            # task_sections | backlog_table |
                            # jsonl_ledger | markdown_note
  - name: ledger-jsonl
    kind: note
    paths: [["docs", "ledger"]]
    patterns: ["**/*.jsonl"]
    parser: jsonl_ledger
    note_kind: auto
  - name: memory
    kind: note
    root: memory            # TASKHUB_MEMORY_ROOT (default ~/.cline/memory)
    patterns: ["*.jsonl"]
    parser: jsonl_ledger
    scope: global
```

`root: memory` sources live under `TASKHUB_MEMORY_ROOT`; `register_project:
false` lets a source feed import without making a folder a project.

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

## Run MCP server

```bash
uv run taskhub-mcp                                  # stdio (default)
TASKHUB_TRANSPORT=http uv run taskhub-mcp           # streamable HTTP on :7801
```

Ports: MCP `127.0.0.1:7801` (`TASKHUB_HOST`/`TASKHUB_PORT`), web
`127.0.0.1:8000` (`TASKHUB_WEB_PORT`).

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

`TASKHUB_DB_URL` above matches the built-in default (`~/.taskhub/taskhub.db`),
so it can be omitted.

## MCP API

Server name `taskhub` (stdio by default, streamable HTTP on `:7801`). In clients
the tools are prefixed `mcp__taskhub__`.

| Group | Tools |
|---|---|
| Tasks | `create_task`, `update_task`, `change_status`, `get_workflow`, `get_context`, `get_next_item`, `list_tasks` |
| Projects | `list_projects`, `project_status`, `evaluate_project`, `forecast_project` |
| Import | `register_projects`, `import_backlog`, `import_all_backlogs`, `import_notes` |
| Notes & docs | `add_note`, `search_notes`, `export_notes`, `upsert_document`, `get_document`, `list_documents` |
| Templates | `list_templates`, `apply_template`, `import_templates` |
| Claims | `register_agent`, `claim_task`, `renew_claim`, `release_task`, `list_claims` |
| Metrics | `ingest_metrics`, `log_activity`, `task_metrics`, `metrics_report` |
| Ops | `backup_db` |

Resources: `task://{id}`, `task://{id}/context`, `project://{slug}/backlog`,
`project://{slug}/notes`, `project://{slug}/specs`, `project://{slug}/claims`.

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
taskhub       — CLI: init-db, list-projects, register-projects, import-all,
                import-notes, import-templates, export, backup
```

Processes over one database. Layout:

```
src/taskhub/
  config.py      pydantic-settings
  db.py          async engine / session
  models.py      SQLAlchemy 2.0 schema
  repo/           thin repositories over AsyncSession
  sources.py     declarative source config (reads sources.yaml)
  services/       scope, prioritizer, gates, claims, documents, templates,
                  evm, forecast, metrics, evaluation, backlog_importer,
                  note_importer (ledgers/notes/memory), export (post digest),
                  backup, metric_ingest
  mcp/            FastMCP server, tools, resources
  api/            FastAPI REST + dashboard (static/index.html)
  cli.py          small human CLI
```

## Database

SQLite by default at `~/.taskhub/taskhub.db` (created by `init-db`). Override with
`TASKHUB_DB_URL`, or point it at PostgreSQL (`postgresql+asyncpg://…`). Migrations
are managed by Alembic.

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

All planned milestones M1–M10 are complete. Suggested follow-ups: SQLite FTS5 for
note search, note filtering in the dashboard, a `portfolio` pseudo-project for
root-level indexes, partitioned metrics for PostgreSQL, and auth in front of the
dashboard.
