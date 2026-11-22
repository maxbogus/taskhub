"""Small human CLI: `taskhub init-db` and `taskhub list-projects`."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from taskhub.config import get_settings
from taskhub.db import init_db, session_scope
from taskhub.mcp_config import render_mcp_config, write_mcp_config
from taskhub.models import ModelPrice
from taskhub.repo.projects import list_projects as repo_list_projects
from taskhub.services.backup import create_backup
from taskhub.services.collect import import_all as import_all_service
from taskhub.services.economics import cost_series as cost_series_service
from taskhub.services.economics import economics_report as economics_report_service
from taskhub.services.economics import economics_summary as economics_summary_service
from taskhub.services.economics import load_price_specs
from taskhub.services.export import export_notes
from taskhub.services.note_importer import import_all_notes
from taskhub.services.price_store import sync_prices as sync_prices_service
from taskhub.services.projects import register_projects as register_projects_service
from taskhub.services.templates import (
    DEFAULT_TEMPLATE_SOURCES,
    import_templates,
)


async def _init_db() -> None:
    await init_db()
    print("database initialized")


async def _list_projects() -> None:
    async with session_scope() as session:
        projects = await repo_list_projects(session)
        payload = [
            {"slug": project.slug, "name": project.name, "root_path": project.root_path}
            for project in projects
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def _mcp_config(out: str, project_dir: str | None, to_stdout: bool) -> None:
    """Write (or print) an MCP client config generated from current settings."""

    directory = Path(project_dir).expanduser() if project_dir else None
    if to_stdout:
        print(render_mcp_config(project_dir=directory), end="")
        return
    target = write_mcp_config(Path(out), project_dir=directory)
    print(json.dumps({"status": "ok", "path": str(target)}, ensure_ascii=False))


async def _import_templates() -> None:
    async with session_scope() as session:
        report = await import_templates(session, source_roots=list(DEFAULT_TEMPLATE_SOURCES))
        await session.commit()
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))


async def _backup(keep: int | None) -> None:
    target = await create_backup(keep=keep)
    print(json.dumps({"status": "ok", "path": str(target)}, ensure_ascii=False))


async def _register_projects(root: str | None, max_depth: int, dry_run: bool) -> None:
    async with session_scope() as session:
        base = Path(root).expanduser() if root else get_settings().repositories_root
        report = await register_projects_service(
            session, base, max_depth=max_depth, dry_run=dry_run
        )
        payload = report.to_dict()
        payload["root"] = str(base)
        payload["dry_run"] = dry_run
        print(json.dumps(payload, ensure_ascii=False, indent=2))


async def _import_all(
    dry_run: bool,
    skip_backup: bool,
    slugs: list[str] | None,
    include_notes: bool = False,
) -> None:
    async with session_scope() as session:
        projects = await repo_list_projects(session)
        if slugs:
            wanted = set(slugs)
            projects = [project for project in projects if project.slug in wanted]
        report = await import_all_service(
            session,
            projects,
            dry_run=dry_run,
            skip_backup=skip_backup,
            include_notes=include_notes,
        )
        payload = report.to_dict()
        payload["dry_run"] = dry_run
        print(json.dumps(payload, ensure_ascii=False, indent=2))


async def _import_notes(dry_run: bool, skip_backup: bool, include_memory: bool) -> None:
    async with session_scope() as session:
        projects = await repo_list_projects(session)
        payload = await import_all_notes(
            session,
            projects,
            include_memory=include_memory,
            dry_run=dry_run,
            skip_backup=skip_backup,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))


async def _export(
    project: str | None,
    kind: str | None,
    ledger_type: str | None,
    since: str | None,
    output_format: str,
    limit: int,
    out: str | None,
) -> None:
    async with session_scope() as session:
        text = await export_notes(
            session,
            project_slug=project,
            kind=kind,
            ledger_type=ledger_type,
            since=since,
            output_format=output_format,
            limit=limit,
        )
    if out:
        target = Path(out).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(
            json.dumps(
                {"status": "ok", "path": str(target), "bytes": len(text)},
                ensure_ascii=False,
            )
        )
    else:
        print(text)


async def _prices_list(model: str | None) -> None:
    """Print the known model prices."""
    async with session_scope() as session:
        rows = list((await session.execute(select(ModelPrice))).scalars().all())
        db_patterns = {row.pattern for row in rows}
        specs = await load_price_specs(session)
    if model:
        needle = model.strip().lower()
        specs = [spec for spec in specs if needle in spec.pattern.lower()]
    payload = [
        {
            **spec.to_dict(),
            "source": "db" if spec.pattern in db_patterns else "yaml",
        }
        for spec in specs
    ]
    print(json.dumps(payload, ensure_ascii=False, indent=2))


async def _prices_sync(dry_run: bool) -> None:
    """Apply the declarative price list to the database."""
    from taskhub.prices import load_prices

    async with session_scope() as session:
        specs = load_prices().to_specs()
        report = await sync_prices_service(session, specs, dry_run=dry_run)
        if not dry_run:
            await session.commit()
    payload = report.to_dict()
    payload["dry_run"] = dry_run
    print(json.dumps(payload, ensure_ascii=False, indent=2))


async def _economics(
    project: str | None,
    group_by: str,
    output_format: str,
    bucket: str,
) -> None:
    """Print the money view of the agent usage."""
    async with session_scope() as session:
        if output_format == "summary":
            payload: Any = await economics_summary_service(session, project_slug=project)
        elif output_format == "series":
            payload = await cost_series_service(session, project_slug=project, bucket=bucket)
        else:
            payload = await economics_report_service(
                session, project_slug=project, group_by=group_by
            )
    if output_format == "markdown":
        print(_economics_markdown(payload))
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _economics_markdown(payload: Any) -> str:
    """Render an economics report as a small markdown table."""
    if isinstance(payload, dict):
        lines = ["| metric | value |", "|---|---|"]
        for key, value in payload.items():
            if isinstance(value, (int, float, str)) or value is None:
                lines.append(f"| {key} | {value} |")
        return "\n".join(lines)
    lines = ["| key | token cost | total cost | sessions |", "|---|---|---|---|"]
    for row in payload:
        lines.append(
            f"| {row['key']} | {row.get('token_cost_usd')} | "
            f"{row.get('total_cost_usd')} | {row.get('sessions')} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(prog="taskhub")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="create database tables")
    subparsers.add_parser("list-projects", help="list registered projects")
    mcp_config_parser = subparsers.add_parser(
        "mcp-config", help="write an MCP client config for this checkout"
    )
    mcp_config_parser.add_argument("--out", default=".mcp.json")
    mcp_config_parser.add_argument("--project-dir", default=None)
    mcp_config_parser.add_argument(
        "--stdout", action="store_true", help="print the config instead of writing it"
    )
    subparsers.add_parser("import-templates", help="import markdown templates")
    backup_parser = subparsers.add_parser("backup", help="create a SQLite backup")
    backup_parser.add_argument("--keep", type=int, default=None)
    register_parser = subparsers.add_parser(
        "register-projects", help="register project directories with task sources"
    )
    register_parser.add_argument("--root", default=None)
    register_parser.add_argument("--max-depth", type=int, default=2)
    register_parser.add_argument("--dry-run", action="store_true")
    import_all_parser = subparsers.add_parser(
        "import-all", help="import backlogs for all registered projects"
    )
    import_all_parser.add_argument("--dry-run", action="store_true")
    import_all_parser.add_argument("--no-backup", action="store_true")
    import_all_parser.add_argument("--project", action="append", default=None)
    import_all_parser.add_argument(
        "--notes", action="store_true", help="also import ledgers and notes"
    )
    notes_parser = subparsers.add_parser(
        "import-notes", help="import ledgers and notes for all projects"
    )
    notes_parser.add_argument("--dry-run", action="store_true")
    notes_parser.add_argument("--no-backup", action="store_true")
    notes_parser.add_argument("--no-memory", action="store_true", help="skip ~/.cline/memory")
    export_parser = subparsers.add_parser("export", help="export a notes digest for writing posts")
    export_parser.add_argument("--project", default=None)
    export_parser.add_argument("--kind", default=None)
    export_parser.add_argument("--ledger-type", default=None)
    export_parser.add_argument("--since", default=None)
    export_parser.add_argument(
        "--format", dest="output_format", choices=("markdown", "json"), default="markdown"
    )
    export_parser.add_argument("--limit", type=int, default=500)
    export_parser.add_argument("--out", default=None)
    prices_parser = subparsers.add_parser("prices", help="show or synchronise the model price list")
    prices_sub = prices_parser.add_subparsers(dest="prices_command", required=True)
    prices_list_parser = prices_sub.add_parser("list", help="show the known model prices")
    prices_list_parser.add_argument("--model", default=None)
    prices_sync_parser = prices_sub.add_parser("sync", help="apply the price list to the database")
    prices_sync_parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="preview the changes (default: on)",
    )
    economics_parser = subparsers.add_parser("economics", help="show what the agents cost")
    economics_parser.add_argument("--project", default=None)
    economics_parser.add_argument("--group-by", default="model")
    economics_parser.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "markdown", "summary", "series"),
        default="json",
    )
    economics_parser.add_argument("--bucket", choices=("day", "week", "month"), default="day")
    args = parser.parse_args()

    if args.command == "init-db":
        asyncio.run(_init_db())
    elif args.command == "list-projects":
        asyncio.run(_list_projects())
    elif args.command == "mcp-config":
        _mcp_config(args.out, args.project_dir, args.stdout)
    elif args.command == "import-templates":
        asyncio.run(_import_templates())
    elif args.command == "backup":
        asyncio.run(_backup(args.keep))
    elif args.command == "register-projects":
        asyncio.run(_register_projects(args.root, args.max_depth, args.dry_run))
    elif args.command == "import-all":
        asyncio.run(_import_all(args.dry_run, args.no_backup, args.project, args.notes))
    elif args.command == "import-notes":
        asyncio.run(_import_notes(args.dry_run, args.no_backup, not args.no_memory))
    elif args.command == "prices":
        if args.prices_command == "list":
            asyncio.run(_prices_list(args.model))
        else:
            asyncio.run(_prices_sync(args.dry_run))
    elif args.command == "economics":
        asyncio.run(_economics(args.project, args.group_by, args.output_format, args.bucket))
    elif args.command == "export":
        asyncio.run(
            _export(
                args.project,
                args.kind,
                args.ledger_type,
                args.since,
                args.output_format,
                args.limit,
                args.out,
            )
        )


if __name__ == "__main__":
    main()
