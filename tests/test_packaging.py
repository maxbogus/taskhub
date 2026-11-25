"""Packaging contract: what the published wheel must contain (P2).

The distribution is published on PyPI as ``taskhub-mcp`` while the import
package stays ``taskhub``. The two YAML files are read through
``importlib.resources`` at runtime, so a wheel without them installs and then
fails on the first command — these tests make that a build failure.
"""

from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _project() -> dict[str, object]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data.get("project")
    assert isinstance(project, dict)
    return project


def test_distribution_name_and_version_match_the_package() -> None:
    project = _project()
    import taskhub

    assert project["name"] == "taskhub-mcp"
    assert project["version"] == taskhub.__version__


def test_license_metadata_points_at_the_licence_file() -> None:
    project = _project()

    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert (ROOT / "LICENSE").is_file()


def test_console_scripts_are_declared() -> None:
    scripts = _project()["scripts"]

    assert scripts == {
        "taskhub": "taskhub.cli:main",
        "taskhub-mcp": "taskhub.mcp.server:run",
        "taskhub-web": "taskhub.api.main:run",
    }


def test_bundled_yaml_files_are_packaged() -> None:
    package = resources.files("taskhub")

    for name in ("sources.yaml", "prices.yaml"):
        assert package.joinpath(name).is_file(), f"{name} missing from the package"


def test_default_configs_load_without_env_overrides(monkeypatch) -> None:
    monkeypatch.delenv("TASKHUB_SOURCES_FILE", raising=False)
    monkeypatch.delenv("TASKHUB_PRICES_FILE", raising=False)
    from taskhub.prices import load_prices
    from taskhub.sources import load_sources

    assert load_sources().sources
    assert load_prices().prices
