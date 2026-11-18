"""Generate MCP client configuration without hardcoded user paths.

The repository ships ``.mcp.json.example`` with portable placeholders; real
``.mcp.json`` files are machine-local (checkout path + database location), so
they are git-ignored and produced by ``taskhub mcp-config`` from the current
settings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from taskhub.config import Settings, get_settings

SERVER_NAME = "taskhub"


def project_root() -> Path:
    """Return the checkout that contains ``pyproject.toml`` (fallback: cwd)."""

    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return Path.cwd()


def resolve_project_dir(project_dir: Path | None = None, settings: Settings | None = None) -> Path:
    """Pick the checkout directory: explicit argument, setting, then detection."""

    settings = settings or get_settings()
    candidate = project_dir or settings.project_dir or project_root()
    return Path(candidate).expanduser().resolve()


def build_mcp_config(
    project_dir: Path | None = None, settings: Settings | None = None
) -> dict[str, Any]:
    """Build an MCP client config for this checkout and the current database."""

    settings = settings or get_settings()
    directory = resolve_project_dir(project_dir, settings)
    return {
        "mcpServers": {
            SERVER_NAME: {
                "command": "uv",
                "args": ["run", "--directory", str(directory), "taskhub-mcp"],
                "env": {
                    "TASKHUB_DB_URL": settings.db_url,
                    "TASKHUB_TRANSPORT": settings.transport,
                },
            }
        }
    }


def render_mcp_config(project_dir: Path | None = None, settings: Settings | None = None) -> str:
    """Render the MCP client config as JSON text."""

    payload = build_mcp_config(project_dir=project_dir, settings=settings)
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def write_mcp_config(
    path: Path, project_dir: Path | None = None, settings: Settings | None = None
) -> Path:
    """Write the MCP client config, creating parent directories as needed."""

    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_mcp_config(project_dir=project_dir, settings=settings), encoding="utf-8"
    )
    return target
