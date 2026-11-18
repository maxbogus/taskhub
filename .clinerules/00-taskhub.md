# TaskHub — project rules for agents

> Project-local rules. Global usage rules: `~/.cline/rules/11-taskhub-tracking.md`.
> Skill: `~/.cline/skills/taskhub-tracker/SKILL.md`.

## Source of truth

The database (`TASKHUB_DB_URL`, default `~/.taskhub/taskhub.db`) is the single
source of truth. Files in `~/Repositories` are **inputs** for idempotent import,
not a second tracker.

## Layout reminders

- `src/taskhub/sources.yaml` — declarative sources (what to scan + parser).
- `src/taskhub/sources.py` — config loader, discovery, parser registry.
- `src/taskhub/services/backlog_importer.py` — task parsing functions.
- `src/taskhub/services/note_importer.py` — ledgers/notes/handoffs/memory.
- `src/taskhub/services/export.py` — markdown/JSON digest for posts.
- `src/taskhub/services/projects.py` — project discovery + README description.
- `docs/COLLECTION_MAP.md` — which folders are imported and where to look.

## Collection model

| Kind | Kind value | Linked to task? |
|---|---|---|
| decisions.jsonl | `decision` | via `tickets` / ids |
| findings.jsonl | `insight` | via ids in text |
| submissions/benchmarks/sessions | `ledger` (`meta.ledger_type`) | via ids |
| HANDOFF.md | `handoff` | via ids |
| docs/notes | `comment` | via ids |
| `.clinerules/*-ledger*.md` | `ledger`, `meta.ledger_type=rule` | — |
| `~/.cline/memory/*.jsonl` | `ledger`, `meta.scope=global` | unique ids only |

Idempotency key: `note.external_ref = "<relative path>:<entry key>"`, unique per
project. Re-running import updates/skips, never duplicates.

## Quality gates

```bash
uv run pytest
uv run ruff check .
uv run mypy
```

All three must pass before a change is considered done. New parsers/importers
require regression tests (`tests/test_note_importer.py`, `tests/test_export.py`,
`tests/test_backlog_importer.py`).

## Schema changes

- Enum columns use `native_enum=False` (text) → new values need no migration.
- New columns / indexes need an Alembic migration in `alembic/versions/`.
- JSON columns use `JsonType` (`JSON` + `JSONB` on PostgreSQL).
