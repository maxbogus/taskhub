# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] — 2026-11-25

First version intended for `pip install`: milestones M12–M16 on top of the
M1–M11 baseline.

### Added

- **Project registry sync (M16):** `sync-projects`, `audit-projects`,
  `move-project`, `archive-project` in the CLI and as MCP tools. Identity is the
  resolved `root_path`, so a known checkout never creates a second slug and a
  folder move keeps every work item attached.
- `discovery.exclude` in `sources.yaml`: snapshots and archive copies never
  become projects.
- Agent economics (M11): declarative model prices, token cost estimation,
  burn-rate series, money-scale EVM, REST endpoints and a dashboard panel.
- MCP evaluation layers (M11–M12): pytest contract, deterministic replay,
  live tool-contract run and agentic scenarios.
- Optional FTS5 index that accelerates note search (M13).
- Hidden `portfolio` pseudo-project for root-level portfolio files (M14).
- Optional API token for the REST API and the dashboard (M15).
- MIT licence file and a GitHub Actions CI workflow.

### Changed

- The PyPI distribution is `taskhub-mcp`; the import package stays `taskhub`
  and the console scripts are `taskhub`, `taskhub-mcp` and `taskhub-web`.
- A project whose root is gone is reported as `detached` instead of being
  silently skipped by the importer.
- `ruff` and `mypy` are pinned in the `dev` extra so the format gate is
  reproducible.

### Fixed

- Import no longer reports success for a project with a missing root without
  saying so.
- Duplicate project registrations are detected, and archiving refuses to hide
  a project that still has work items.

## [0.1.0] — 2026-11-18

M1–M11 baseline: database schema and Alembic migrations, FastMCP server, RICE
prioritisation, EVM and Monte-Carlo forecast, idempotent backlog import,
metrics, role-aware gates, notes/documents/templates, claim leases, REST API and
dashboard, Docker deployment, backups.

[Unreleased]: https://github.com/maxbogus/taskhub/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/maxbogus/taskhub/releases/tag/v0.2.0
