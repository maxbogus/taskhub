# Project registry sync — policy and the 2026-11 migration

> **Model:** `Project.slug` is immutable; `Project.root_path` is the location.
> Identity during discovery is the resolved root path, so a known checkout never
> produces a second project.
>
> **Current health:** `docs/PROJECT_AUDIT.md` (generated, do not hand-edit):
> `uv run taskhub audit-projects --root ~/Repositories --format markdown --out docs/PROJECT_AUDIT.md`

## Why this exists

An earlier import populated the registry from a flat `~/Repositories/*` layout
and, for several projects, the source files were deleted afterwards (the commit
message said "remove docs/backlog after TaskHub migration"). The portfolio was
then reorganised into containers (`career/`, `hackathons/`, `legal/`,
`work_projects/`, `study/`, `landings/`, `infra/`, `workshops/`) and the registry
kept the old paths.

Consequences measured before the fix:

| Metric | Before | After |
|---|---|---|
| Registered projects | 88 | 88 |
| Active / archived | 88 / 0 | 61 / 27 |
| Active with a missing root | 34 | 3 (intentionally detached) |
| Work items on missing roots | 975 of 1849 (53 %) | 6 of 1849 |
| Same root registered twice | 1 + 25 twin pairs after a move | 0 |
| Identical notes in two projects | 538 extra copies | archived with the twins |

`get_next_item(cwd=...)` resolves the longest matching active root, so those 34
dead paths answered "no available items" for roughly half the portfolio, while
their work items sat on projects nobody could resolve.

## Policy A — re-point, never move work items

1. `slug` is immutable. `root_path` changes with an explicit move.
2. Moving a project never touches `work_item` rows: items stay attached to the
   same `Project.id`, so priorities, dependencies, notes and claims survive.
3. A duplicate (two active projects on one resolved root) is **archived**, not
   merged. Archiving is soft (`archived_at`), reversible, and refuses to hide a
   project that still has work items or notes that are not copies of the keeper.
4. A project whose root is gone is **detached**: it stays active, keeps its
   history, is flagged in `project.config.detached`, is skipped by the importer
   and is reported instead of silently importing nothing.
5. `archive-only` is forbidden for the datasets whose files were deleted after
   the earlier import: the database is the only copy of those items, so hiding
   them would lose the work from view.

## Applied on 2026-11-25

The mapping is machine-specific and lives outside the repository
(`scripts/migrations/2026-11-projects.yaml`, gitignored): 30 moves with targets
relative to `--root`.

```bash
make backup
uv run taskhub sync-projects --root ~/Repositories \
  --moves scripts/migrations/2026-11-projects.yaml \
  --archive-duplicates --dry-run          # reviewed first
uv run taskhub sync-projects --root ~/Repositories \
  --moves scripts/migrations/2026-11-projects.yaml \
  --archive-duplicates
uv run taskhub archive-project --slug <eval-seed> --force
uv run taskhub import-all --notes --no-backup
```

| Result | Value |
|---|---|
| Projects re-pointed | 30 (25 twins + 5 moves without a twin) |
| Empty twins archived | 26 |
| Explicitly archived | 1 (an eval seed project under `/tmp`) |
| Detached (root gone, data kept) | 3 |
| Work items preserved | 1849 of 1849 (0 on archived projects) |
| Duplicate roots among active projects | 0 |
| Import after the change | 1 created, 56 updated, 1023 skipped, 0 errors, 3 detached |

Spot check: `get_next_item(cwd=...)` was run in five repositories that used to
report "no available items"; each now resolves its project and returns the
first item. The audit (3 detached projects, 0 duplicates, 0 uncovered
directories) is the regression check.

## The daily check

```bash
# Read-only: stale roots, duplicates, uncovered directories, orphaned work.
uv run taskhub audit-projects --root ~/Repositories

# Read-only: what a sync would do (moves, archives, registrations).
uv run taskhub sync-projects --root ~/Repositories --max-depth 3 --dry-run
```

`discovery.exclude` in `src/taskhub/sources.yaml` keeps published snapshots
(package snapshots under `workshops/`) and archive copies (`conf/archive/*`)
out of the registry: they carry the handoff of their source repository and
would duplicate notes and shadow the real project in scope resolution.

## Rollback

* Full: restore `~/.taskhub/backups/taskhub-*.db` (newest file taken before the run).
* Single project: `uv run taskhub archive-project --slug <slug> --restore`, and
  `uv run taskhub move-project --slug <slug> --root <old path>`.
* Nothing is ever hard-deleted: archived projects and their notes remain in the
  database and are returned by `list_projects(include_archived=True)`.
