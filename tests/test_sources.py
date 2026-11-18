"""Tests for the declarative YAML source configuration."""

from __future__ import annotations

from pathlib import Path

import yaml

from taskhub.sources import (
    SourcesConfig,
    SourceSpec,
    discover_sources,
    load_sources,
    matching_sources,
)


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_default_config_has_task_note_and_memory_sources() -> None:
    config = load_sources()

    task_names = {spec.name for spec in config.for_kind("task")}
    note_names = {spec.name for spec in config.for_kind("note")}
    memory_names = {spec.name for spec in config.for_kind("note", "memory")}

    assert {"ai-tasks", "ai-analysis", "readme-tasks"} <= task_names
    assert {"ledger-jsonl", "handoff", "ledger-rules"} <= note_names
    assert "memory" in memory_names


def test_override_config_replaces_sources(tmp_path: Path) -> None:
    path = tmp_path / "sources.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "name": "custom",
                        "kind": "task",
                        "files": [["CUSTOM.md"]],
                        "parser": "task_sections",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    config = load_sources(str(path))

    assert [spec.name for spec in config.sources] == ["custom"]
    assert config.sources[0].parser == "task_sections"
    # Defaults from the file's `defaults:` block are applied to each spec.
    assert config.sources[0].patterns == config.defaults.patterns


def test_discover_sources_uses_config_not_code(tmp_path: Path) -> None:
    _write(tmp_path / "CUSTOM.md", "## Tasks\n- [ ] Do it")
    _write(tmp_path / "ai" / "tasks" / "x.md", "# X\n")
    config = SourcesConfig(
        sources=[SourceSpec(name="custom", kind="task", files=(("CUSTOM.md",),))]
    )

    found = discover_sources(tmp_path, "task", config=config)

    assert [item.path.name for item in found] == ["CUSTOM.md"]
    assert found[0].spec.name == "custom"


def test_discover_requires_task_section_when_configured(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "# Project\n\nNothing to do.\n")
    config = SourcesConfig(
        sources=[
            SourceSpec(
                name="readme",
                kind="task",
                files=(("README.md",),),
                requires_task_section=True,
            )
        ]
    )

    assert discover_sources(tmp_path, "task", config=config) == []

    _write(tmp_path / "README.md", "# Project\n\n## Roadmap\n- [ ] Do it\n")
    assert len(discover_sources(tmp_path, "task", config=config)) == 1


def test_default_discovery_skips_templates_and_navigation(tmp_path: Path) -> None:
    _write(tmp_path / "ai" / "tasks" / "templates" / "TASK.md", "# T\n")
    _write(tmp_path / "ai" / "tasks" / "INDEX.md", "# Index\n")
    _write(tmp_path / "ai" / "tasks" / "real.md", "# Real\n")

    names = {item.path.name for item in discover_sources(tmp_path, "task")}

    assert "real.md" in names
    assert "TASK.md" not in names
    assert "INDEX.md" not in names


def test_matching_sources_respects_register_and_patterns(tmp_path: Path) -> None:
    config = SourcesConfig(
        sources=[
            SourceSpec(name="ai", kind="task", paths=(("ai", "tasks"),)),
            SourceSpec(
                name="readme",
                kind="task",
                files=(("README.md",),),
                register_project=False,
            ),
            SourceSpec(
                name="rules",
                kind="note",
                paths=((".clinerules",),),
                patterns=("*ledger*.md",),
            ),
        ]
    )
    project = tmp_path / "p"
    _write(project / "ai" / "tasks" / "x.md", "# X\n")
    _write(project / "README.md", "# P\n")
    _write(project / ".clinerules" / "other.md", "x\n")

    assert matching_sources(project, config=config) == ["ai"]

    _write(project / ".clinerules" / "14-ledger.md", "x\n")
    assert matching_sources(project, config=config) == ["ai", "rules"]
