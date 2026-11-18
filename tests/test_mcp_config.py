"""Tests for machine-local MCP client config generation."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from taskhub.cli import main
from taskhub.config import DEFAULT_DB_URL, DEFAULT_HOME, Settings
from taskhub.mcp_config import (
    build_mcp_config,
    project_root,
    render_mcp_config,
    resolve_project_dir,
    write_mcp_config,
)


def _settings_without_env(monkeypatch, **overrides: Any) -> Settings:
    for name in ("TASKHUB_HOME", "TASKHUB_DB_URL", "TASKHUB_BACKUP_DIR"):
        monkeypatch.delenv(name, raising=False)
    return Settings(_env_file=None, **overrides)


def test_settings_derive_paths_from_taskhub_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TASKHUB_HOME", str(tmp_path / "data"))

    settings = Settings(_env_file=None)

    home = tmp_path / "data"
    assert settings.home == home
    assert settings.db_url == f"sqlite+aiosqlite:///{home / 'taskhub.db'}"
    assert settings.backup_dir == home / "backups"


def test_settings_keep_explicit_path_overrides(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TASKHUB_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("TASKHUB_DB_URL", "postgresql+asyncpg://user@host/taskhub")
    monkeypatch.setenv("TASKHUB_BACKUP_DIR", str(tmp_path / "custom-backups"))

    settings = Settings(_env_file=None)

    assert settings.db_url == "postgresql+asyncpg://user@host/taskhub"
    assert settings.backup_dir == tmp_path / "custom-backups"


def test_settings_defaults_are_portable(monkeypatch) -> None:
    settings = _settings_without_env(monkeypatch)

    assert settings.home == DEFAULT_HOME
    assert settings.db_url == DEFAULT_DB_URL
    assert settings.backup_dir == DEFAULT_HOME / "backups"


def test_build_mcp_config_uses_given_paths(tmp_path: Path, monkeypatch) -> None:
    settings = _settings_without_env(monkeypatch, home=tmp_path / "data")
    project_dir = tmp_path / "checkout"

    config = build_mcp_config(project_dir=project_dir, settings=settings)

    server = config["mcpServers"]["taskhub"]
    assert server["command"] == "uv"
    assert server["args"] == [
        "run",
        "--directory",
        str(project_dir.resolve()),
        "taskhub-mcp",
    ]
    assert server["env"] == {
        "TASKHUB_DB_URL": settings.db_url,
        "TASKHUB_TRANSPORT": "stdio",
    }
    assert str(Path.home()) not in render_mcp_config(project_dir, settings)


def test_resolve_project_dir_prefers_argument_then_setting(tmp_path: Path, monkeypatch) -> None:
    settings = _settings_without_env(monkeypatch, project_dir=tmp_path / "from-settings")

    assert resolve_project_dir(settings=settings) == (tmp_path / "from-settings").resolve()
    assert resolve_project_dir(tmp_path / "explicit", settings) == (tmp_path / "explicit").resolve()
    assert (project_root() / "pyproject.toml").is_file()


def test_write_mcp_config_creates_parent_dirs(tmp_path: Path, monkeypatch) -> None:
    settings = _settings_without_env(monkeypatch)
    target = tmp_path / "nested" / ".mcp.json"

    written = write_mcp_config(target, project_dir=tmp_path / "checkout", settings=settings)

    assert written == target
    text = target.read_text(encoding="utf-8")
    assert text.endswith("\n")
    payload = json.loads(text)
    assert payload["mcpServers"]["taskhub"]["env"]["TASKHUB_DB_URL"] == settings.db_url


def test_cli_mcp_config_writes_and_prints(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["taskhub", "mcp-config", "--project-dir", str(tmp_path / "checkout")],
    )
    monkeypatch.chdir(tmp_path)

    main()

    written = json.loads(capsys.readouterr().out)
    assert written["status"] == "ok"
    assert (tmp_path / ".mcp.json").is_file()

    monkeypatch.setattr(
        sys,
        "argv",
        ["taskhub", "mcp-config", "--stdout", "--project-dir", str(tmp_path / "checkout")],
    )
    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["mcpServers"]["taskhub"]["args"][2] == str((tmp_path / "checkout").resolve())
