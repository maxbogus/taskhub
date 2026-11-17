"""Small human CLI: `taskhub init-db` and `taskhub list-projects`."""

from __future__ import annotations

import argparse
import asyncio
import json

from taskhub.db import init_db, session_scope
from taskhub.repo.projects import list_projects as repo_list_projects


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


def main() -> None:
    parser = argparse.ArgumentParser(prog="taskhub")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="create database tables")
    subparsers.add_parser("list-projects", help="list registered projects")
    args = parser.parse_args()

    if args.command == "init-db":
        asyncio.run(_init_db())
    elif args.command == "list-projects":
        asyncio.run(_list_projects())


if __name__ == "__main__":
    main()
