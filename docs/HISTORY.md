# Milestone history

A curated narrative of how TaskHub grew. The git log has the detail; this file
is the version-independent summary (useful when a public tree is published
without the full history).

| Milestone | Scope | Result |
|---|---|---|
| M1 | Scaffold | schema, Alembic, FastMCP server, `create_task` / `get_context` / `get_next_item` / `list_projects`, scope resolution by `cwd` |
| M2 | Evaluation core | RICE prioritisation, EVM (CPI/SPI/EAC/ETC/VAC in `Decimal`), Monte-Carlo duration forecast |
| M3 | Import | idempotent backlog import driven by `sources.yaml`, online SQLite backup before a run |
| M4 | Metrics | Cline task-history ingestion (tokens, model, cost, active time), per-task and per-project reports |
| M5 | Workflow | role-aware transitions with gates, audit note on every change, per-project flow overrides |
| M6 | Notes and documents | notes, documents, templates, `project://{slug}/notes` and `/specs` resources |
| M7 | Claims | TTL claim leases; expired claims never block work |
| M8 | Web | FastAPI REST API and a single-page dashboard |
| M9 | Deploy | Docker (non-root, healthchecks), backup retention |
| M10 | Collection | ledger/notes import (jsonl, handoffs, sessions, `.clinerules`), digest export for posts |
| M11 | Agent economics | declarative prices, token cost estimation, burn-rate series, money-scale EVM, price CLI/REST |
| M12 | Eval | MCP tool-contract eval in pytest and promptfoo, plus an agentic scenario layer |
| M13 | Note search | optional FTS5 index as an accelerator (answers never change, only speed) |
| M14 | Portfolio | hidden `portfolio` pseudo-project for root-level files |
| M15 | API auth | optional `TASKHUB_TOKEN` for the REST API and dashboard |
| M16 | Registry sync | root-path identity, `sync-projects` / `audit-projects` / `move-project` / `archive-project`, `discovery.exclude`, detached reporting, licence and CI |

## Design decisions worth keeping

- **Soft archive only.** Nothing is hard-deleted; a project or task is hidden by
  filling `archived_at`.
- **The slug is immutable, the root path moves.** Work items follow the project,
  not the folder.
- **Silence is a bug.** A missing root, an unpriced model or an empty backlog is
  reported, never assumed to be "no work".
- **Money is integer micro-USD.** `Decimal` never touches SQLite.
- **Eval is deterministic.** The replay layer is the CI gate; the live-model
  layer is opt-in (`slow`).
