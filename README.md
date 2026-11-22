# TaskHub

Local MCP-native single source of truth for tasks, projects, notes and metrics.
Replaces scattered file backlogs (`*/ai/tasks/`, `*/docs/backlog/`,
`shared-infrastructure/tasks/`), ledgers (`*/docs/ledger/*.jsonl`) and handoffs
with one database and one workflow. Which files are collected is declared in
`sources.yaml`; the per-folder map is `docs/COLLECTION_MAP.md`.

## Status (M1–M11)

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
`/api/tasks/{id}`, `/api/metrics`, `/api/economics`, `/api/economics/summary`,
`/api/economics/series`) and a single-page dashboard served at `/`;
`taskhub-web` entry point.

M9: Docker deployment (multi-stage, non-root, healthchecks) and backup retention —
`taskhub backup --keep N`, `backup_keep` setting, `backup_db(keep=...)`.

M11: agent economics — model price list (`prices.yaml` + `model_price`
table), token cost estimation, project rates, money-scale EVM, burn-rate
series, MCP tools `economics_report`/`economics_summary`/`cost_series`/
`list_prices`/`sync_prices`/`set_model_price`/`close_model_price`/
`set_project_economics`, REST `/api/economics*`, CLI `taskhub prices` and
`taskhub economics`, and a cost panel in the dashboard.

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
uv run taskhub init-db          # create tables under $TASKHUB_HOME (default ~/.taskhub)
uv run alembic upgrade head     # or apply migrations (recommended)
uv run taskhub mcp-config       # write a machine-local .mcp.json for MCP clients
```

No environment file is required — every setting has a working default (see below).

## Defaults

Zero-config: the app runs with these defaults. Override any setting with the
`TASKHUB_*` environment variable (or copy `.env.example` to `.env`).

| Setting | Env var | Default |
|---|---|---|
| Data dir | `TASKHUB_HOME` | `$HOME/.taskhub` |
| Database | `TASKHUB_DB_URL` | `sqlite+aiosqlite:///$TASKHUB_HOME/taskhub.db` |
| Backup dir | `TASKHUB_BACKUP_DIR` | `$TASKHUB_HOME/backups` |
| Backups to keep | `TASKHUB_BACKUP_KEEP` | `7` (`0` = keep all) |
| MCP transport | `TASKHUB_TRANSPORT` | `stdio` (`http` = streamable HTTP) |
| Host | `TASKHUB_HOST` | `127.0.0.1` |
| MCP port | `TASKHUB_PORT` | `7801` |
| Web port | `TASKHUB_WEB_PORT` | `8000` |
| Projects root | `TASKHUB_REPOSITORIES_ROOT` | `$HOME/Repositories` |
| Memory root | `TASKHUB_MEMORY_ROOT` | `$HOME/.cline/memory` |
| Sources file | `TASKHUB_SOURCES_FILE` | `src/taskhub/sources.yaml` |
| Checkout dir | `TASKHUB_PROJECT_DIR` | auto-detected from `pyproject.toml` |
| Metrics sources | `TASKHUB_METRICS_SOURCES` | `cline` |
| Claim TTL (min) | `TASKHUB_DEFAULT_CLAIM_TTL_MINUTES` | `30` |
| Log level | `TASKHUB_LOG_LEVEL` | `INFO` |
| Prices file | `TASKHUB_PRICES_FILE` | `src/taskhub/prices.yaml` |
| Human rate (USD/h) | `TASKHUB_HUMAN_RATE_USD_PER_HOUR` | `50` (placeholder) |
| Error cost (USD) | `TASKHUB_ERROR_COST_USD` | `0` (errors not billed) |
| Supervision cost (USD) | `TASKHUB_SUPERVISION_COST_USD` | `0` (not measured) |

## Startup scripts & scenarios

| Scenario | Command | Endpoint |
|---|---|---|
| First run | `uv run taskhub init-db` | — |
| Discover + import tasks | `uv run taskhub register-projects --root ~/Repositories` then `uv run taskhub import-all --dry-run` then `uv run taskhub import-all` | — |
| Tasks + ledgers/notes | `uv run taskhub import-all --notes` | — |
| Ledgers/notes only | `uv run taskhub import-notes` | — |
| MCP server (stdio) | `uv run taskhub-mcp` | stdio |
| MCP server (HTTP) | `TASKHUB_TRANSPORT=http uv run taskhub-mcp` | `http://127.0.0.1:7801` |
| MCP client config | `uv run taskhub mcp-config` | writes `.mcp.json` |
| Web API + dashboard | `uv run taskhub-web` | `http://127.0.0.1:8000` |
| Export digest | `uv run taskhub export --project <slug> --format markdown --out out.md` | — |
| Backup | `uv run taskhub backup --keep 7` | `$TASKHUB_HOME/backups` |
| Model prices | `uv run taskhub prices list` | — |
| Sync prices | `uv run taskhub prices sync --no-dry-run` | — |
| Agent economics | `uv run taskhub economics --project <slug>` | — |
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

MCP client config is machine-local (it contains the checkout path and database
location), so it is generated from the current settings and git-ignored:

```bash
uv run taskhub mcp-config              # write .mcp.json for this checkout
uv run taskhub mcp-config --stdout     # print it instead
uv run taskhub mcp-config --out other-mcp.json --project-dir ~/src/taskhub
```

The generated file (same shape as the committed `.mcp.json.example`):

```json
{
  "mcpServers": {
    "taskhub": {
      "command": "uv",
      "args": ["run", "--directory", "<checkout>", "taskhub-mcp"],
      "env": {
        "TASKHUB_DB_URL": "sqlite+aiosqlite:///<TASKHUB_HOME>/taskhub.db",
        "TASKHUB_TRANSPORT": "stdio"
      }
    }
  }
}
```

`TASKHUB_DB_URL` above is the resolved default (`$TASKHUB_HOME/taskhub.db`), so
it can be omitted. `.mcp.json.example` uses `${workspaceFolder}` for clients that
support workspace-variable expansion; otherwise run `taskhub mcp-config` once per
machine.

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

### Agent economics tools

| Tool | Purpose |
|---|---|
| `economics_report` | Money view grouped by model/agent/task/project |
| `economics_summary` | Totals, human baseline, savings, ROI, `progress_money` |
| `cost_series` | Burn-rate series (`day`/`week`/`month`) |
| `list_prices` | Known prices (database first, then `prices.yaml`) |
| `sync_prices` | Apply the price list (dry run by default) |
| `set_model_price` | Create or update a price (never deletes) |
| `close_model_price` | Close a price by setting its end date |
| `set_project_economics` | Set the human rate, error price and supervision cost |

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
| `GET /api/economics?project=&group_by=` | Money view of the agent usage |
| `GET /api/economics/summary?project=` | Totals, baseline, savings, ROI |
| `GET /api/economics/series?project=&bucket=` | Burn-rate series |
| `GET /` | Single-page dashboard (with the cost panel) |

## Architecture

```
taskhub-mcp   — MCP server (stdio / streamable HTTP :7801)
taskhub-web   — FastAPI REST + dashboard (:8000)
taskhub       — CLI: init-db, list-projects, mcp-config, register-projects,
                import-all, import-notes, import-templates, export, backup,
                prices list|sync, economics
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
                  backup, metric_ingest,
                  cost_estimator, economics, price_store
  prices.py       declarative model price list (prices.yaml)
  mcp/            FastMCP server, tools, resources
  api/            FastAPI REST + dashboard (static/index.html)
  cli.py          small human CLI
```

## Database

SQLite by default at `$TASKHUB_HOME/taskhub.db` (created by `init-db`). Override
with `TASKHUB_DB_URL`, or point it at PostgreSQL (`postgresql+asyncpg://…`).
Migrations are managed by Alembic.

## Agent economics (M11)

The economics layer answers two questions: **what did the agents cost?**
and **was that cheaper than doing it by hand?**

### Where the numbers come from

| Part | Source |
|---|---|
| Token cost | Recomputed from the price list (`src/taskhub/prices.yaml` → `model_price` table) |
| Reported cost | `Metric.cost_usd` as written by the tool — kept **separate** from the estimate |
| Error cost | `failures × TASKHUB_ERROR_COST_USD` (default `0`) |
| Supervision cost | Flat `TASKHUB_SUPERVISION_COST_USD` per project (default `0`) |
| Human baseline | `estimate_hours × human rate` — what the same work would cost a person |

Rates come from `TASKHUB_HUMAN_RATE_USD_PER_HOUR` (default `50`, a
**placeholder**) and can be overridden per project:

```json
{"config": {"economics": {"human_rate_usd_per_hour": 75, "error_cost_usd": 40}}}
```

### Two rules that keep the numbers honest

1. **An unpriced model costs nothing *and is reported*.** `token_cost_usd`
   is `null`, the model lands in `unpriced_models`, and
   `unpriced_token_share` shows how much of the work is unaccounted for.
   Pricing unknown models at zero would silently understate the cost.
2. **Reported and estimated cost never overwrite each other.** Both are
   in every report, so a discrepancy with vendor billing stays visible.

### Prices

Prices live in `src/taskhub/prices.yaml` (declarative, override with
`TASKHUB_PRICES_FILE`) and are stored as **micro-USD per 1M tokens** —
integers, because the project forbids floats in monetary arithmetic and
SQLite cannot store `Decimal` natively.

```yaml
prices:
  - pattern: "claude-*"      # glob over Metric.model
    provider: anthropic
    input_usd_per_mtok: 3.0
    output_usd_per_mtok: 15.0
    valid_from: "2026-01-01T00:00:00"
```

The most specific pattern wins (exact name over glob); a closed price
(`valid_to`) stops applying but is never deleted.

```bash
uv run taskhub prices list            # database + bundled list
uv run taskhub prices sync             # dry run (default)
uv run taskhub prices sync --no-dry-run
```

### Money EVM

`project_status` and `evaluate_project` carry `progress_money` next to the
hourly `progress`. It is **not** the classic CPI: earned value is the
human baseline while actual cost is the real agent spend, so the index
answers *build-vs-buy*, not "are we inside the budget".

### Eval of the MCP tool contract

The MCP surface is covered by three layers, all deterministic:

| Layer | What runs | Command |
|---|---|---|
| 1 — pytest contract | Every case as a real `call_tool` invocation | `uv run pytest tests/test_eval_contract.py` |
| 2a — deterministic replay | promptfoo `exec:` provider, no model, no server | `make eval-mock` |
| 2b — real MCP calls | promptfoo `mcp` provider launches the TaskHub server | `make eval` |

The cases live in `eval/cases/tool_contract.jsonl` (single source of
truth); `scripts/build_promptfoo_cases.py` converts them for promptfoo.
Layer 2b needs `npm install` once (promptfoo is a dev dependency).

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
make test          # pytest
make lint          # ruff check
make type-check    # mypy
make check         # lint + type-check + test
make secrets       # gitleaks: scan git history
make secrets-scan  # gitleaks: scan the working tree
```

Run `make help` (or just `make`) for all targets — install, init-db, test, lint,
format, type-check, check, secrets, register-projects, import, import-notes,
export, backup, mcp, web, docker-up/down.

## Design notes

- Soft archive only: nothing is deleted physically.
- Enum stored as text on SQLite (`native_enum=False`).
- JSON uses `JSONB` on PostgreSQL and `JSON` elsewhere.
- `WorkItem.due_at` is used for EVM planned value and lead time.

## Milestones

All planned milestones M1–M11 are complete. Suggested follow-ups: an
agentic eval layer on a live model (M12), ruble conversion for the money
view, SQLite FTS5 for note search, note filtering in the dashboard, a
`portfolio` pseudo-project for root-level indexes, partitioned metrics for
PostgreSQL, and auth in front of the dashboard.
