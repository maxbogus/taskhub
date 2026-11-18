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
  sources.py      declarative source config (reads sources.yaml)
  repo/           projects, work_items, notes, documents, templates,
                  claims, agents
  services/       scope (cwd → project), prioritizer (RICE), gates (roles,
                  transitions, audit notes), claims (TTL leases), documents
                  (versioned upsert), templates (import/apply), evm (Decimal),
                  forecast (Monte Carlo), metrics, evaluation, backlog_importer,
                  note_importer (ledgers/notes/memory), export (post digest),
                  backup (retention), metric_ingest
  mcp/            server.py (FastMCP), tools.py, resources.py
  api/            main.py (FastAPI REST + dashboard), static/index.html
  cli.py          `taskhub init-db|list-projects|import-templates|backup`
tests/            pytest (async, in-memory/file SQLite)
alembic/          async migrations
```

## Commands

```bash
uv sync --extra dev
uv run taskhub init-db
uv run taskhub register-projects --root ~/Repositories   # discover projects
uv run taskhub import-all --dry-run                      # preview collection
uv run taskhub import-all                                # bulk backlog import
uv run taskhub import-all --notes                        # tasks + ledgers/notes
uv run taskhub import-notes                              # ledgers/notes/memory only
uv run taskhub export --format markdown --out out.md     # notes digest for posts
uv run taskhub-mcp                       # stdio MCP
TASKHUB_TRANSPORT=http uv run taskhub-mcp
uv run taskhub-web                       # REST + dashboard (:8000)
uv run taskhub import-templates
uv run taskhub backup --keep 7
docker compose up -d --build             # web + MCP
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

## Notes, ledgers and export

- `services/note_importer.py` imports `docs/ledger/*.jsonl`, `docs/HANDOFF.md`,
  `docs/notes/`, `docs/sessions/`, `.clinerules/*-ledger*.md` and
  `~/.cline/memory/*.jsonl` (pseudo-project `memory`).
- Notes are schema-agnostic, linked to tasks via `tickets` ids / task-id tokens,
  and upserted idempotently by `(project_id, external_ref)`.
- `services/export.py` renders a markdown/JSON digest (notes + linked tasks)
  for writing posts: `taskhub export --project <slug> --format markdown`.
- Per-folder source map: `docs/COLLECTION_MAP.md`.
- Sources (what to scan + which parser) live in `src/taskhub/sources.yaml`;
  override with `TASKHUB_SOURCES_FILE`. No source lists are hardcoded in Python.

## Milestones

M1 scaffold (done) → M2 evaluation core (done) → M3 import (done) → M4 metrics (done) →
M5 gates (done) → M6 notes/docs/templates (done) → M7 claims (done) →
M8 dashboard (done) → M9 deploy/backup (done) → M10 ledgers/notes + export (done).
