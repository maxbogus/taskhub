"""Small human CLI: `taskhub init-db` and `taskhub list-projects`."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from taskhub.config import get_settings
from taskhub.db import init_db, session_scope
from taskhub.repo.projects import list_projects as repo_list_projects
from taskhub.services.backup import create_backup
from taskhub.services.collect import import_all as import_all_service
from taskhub.services.export import export_notes
from taskhub.services.note_importer import import_all_notes
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="taskhub")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="create database tables")
    subparsers.add_parser("list-projects", help="list registered projects")
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
    args = parser.parse_args()

    if args.command == "init-db":
        asyncio.run(_init_db())
    elif args.command == "list-projects":
        asyncio.run(_list_projects())
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
