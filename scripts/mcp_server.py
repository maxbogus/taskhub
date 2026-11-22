#!/usr/bin/env python3
"""Run the TaskHub MCP server over stdio for the eval harness.

Promptfoo launches an MCP server from a ``.py`` file with the system
``python3`` (see its client: ``command = isPy ? "python3" : process.execPath``).
That interpreter does not have the project's dependencies, so this script
re-executes itself with the project virtualenv before starting the
server.

The database is a throwaway one under ``TASKHUB_HOME``-independent
``TASKHUB_EVAL_DB`` path, seeded with a single ``demo`` project so the
eval cases have something to talk about. The seed is idempotent.

Usage:
    python scripts/mcp_server.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"


def _reexec_in_venv() -> None:
    """Re-execute this script with the project virtualenv interpreter.

    Promptfoo starts ``.py`` MCP servers with the system ``python3``,
    which lacks the project dependencies (and may have a different
    version of the MCP SDK). Re-executing keeps the configuration free
    of absolute interpreter paths.

    The interpreter is compared by its *path inside the virtualenv*, not
    by resolving symlinks: ``.venv/bin/python`` is usually a symlink to
    the system interpreter, so a resolved comparison would wrongly
    conclude that we are already inside the venv.
    """
    if not VENV_PYTHON.is_file():
        return
    if VENV_PYTHON.parent.parent == Path(sys.prefix):
        return
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve())])


def _prepare_database() -> None:
    """Create the eval database and seed the ``demo`` project."""
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from sqlalchemy import select

    from taskhub.db import init_db, session_scope
    from taskhub.models import Project
    from taskhub.repo.projects import create_project

    async def seed() -> None:
        await init_db()
        async with session_scope() as session:
            existing = (
                await session.execute(select(Project).where(Project.slug == "demo"))
            ).scalar_one_or_none()
            if existing is None:
                await create_project(session, slug="demo", name="Demo", root_path="/tmp/demo")
                await session.commit()

    import asyncio

    asyncio.run(seed())


def main() -> int:
    """Start the MCP server on stdio."""
    _reexec_in_venv()
    _prepare_database()
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from taskhub.mcp.server import run

    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
