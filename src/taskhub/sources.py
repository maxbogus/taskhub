"""Declarative source configuration, loaded from YAML.

All task/note sources and their parser bindings live in ``sources.yaml``;
this module only reads and interprets that file. Override it with
``TASKHUB_SOURCES_FILE``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from taskhub.config import get_settings

SourceKind = Literal["task", "note"]
SourceRoot = Literal["project", "memory"]
Parser = Callable[[Path, Path, "SourceSpec"], list[Any]]

PACKAGE = "taskhub"
DEFAULT_CONFIG_NAME = "sources.yaml"

DEFAULT_INCLUDE_PATTERNS = ("**/*.md",)


class SourceDefaults(BaseModel):
    """Scanning defaults shared by all sources."""

    patterns: tuple[str, ...] = DEFAULT_INCLUDE_PATTERNS
    exclude: tuple[str, ...] = (
        "templates",
        "node_modules",
        ".git",
        ".venv",
        "venv",
        "__pycache__",
    )
    skip_files: tuple[str, ...] = (
        "INDEX.md",
        "CURRENT.md",
        "README.md",
        "IMPLEMENTATION-PLAN.md",
        "TASK-TEMPLATE.md",
        "LEAN-TEMPLATE.md",
        "DEPLOYMENT-CHECKLIST.md",
    )


class DiscoveryConfig(BaseModel):
    """Folders that must never be registered as projects."""

    exclude: tuple[str, ...] = ()


class SourceSpec(BaseModel):
    """One declarative source: what to scan and which parser to use."""

    name: str
    kind: SourceKind
    root: SourceRoot = "project"
    paths: tuple[tuple[str, ...], ...] = ()
    files: tuple[tuple[str, ...], ...] = ()
    patterns: tuple[str, ...] | None = None
    exclude: tuple[str, ...] | None = None
    skip_files: tuple[str, ...] | None = None
    parser: str = "auto"
    note_kind: str | None = None
    ledger_type: str | None = None
    scope: Literal["project", "global"] = "project"
    requires_task_section: bool = False
    register_project: bool = True


class SourcesConfig(BaseModel):
    """The full parsed source configuration."""

    defaults: SourceDefaults = Field(default_factory=SourceDefaults)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    sources: list[SourceSpec] = Field(default_factory=list)

    def for_kind(self, kind: SourceKind, source_root: SourceRoot = "project") -> list[SourceSpec]:
        return [spec for spec in self.sources if spec.kind == kind and spec.root == source_root]


@dataclass(frozen=True)
class DiscoveredSource:
    """A concrete file paired with the spec that selected it."""

    path: Path
    spec: SourceSpec


def default_config_path() -> Path:
    """Path of the bundled ``sources.yaml``."""

    bundled = resources.files(PACKAGE).joinpath(DEFAULT_CONFIG_NAME)
    return Path(str(bundled))


@lru_cache(maxsize=8)
def load_sources(path: str | None = None) -> SourcesConfig:
    """Load sources config; ``path=None`` uses env override or the bundle."""

    if path is None:
        override = get_settings().sources_file
        file_path = Path(override).expanduser() if override else default_config_path()
    else:
        file_path = Path(path).expanduser()
    if not file_path.is_file():
        raise FileNotFoundError(f"sources config not found: {file_path}")

    data = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    config = SourcesConfig.model_validate(data)
    defaults = config.defaults
    for spec in config.sources:
        if spec.patterns is None:
            spec.patterns = defaults.patterns
        if spec.exclude is None:
            spec.exclude = defaults.exclude
        if spec.skip_files is None:
            spec.skip_files = defaults.skip_files
    return config


GENERIC_PATTERNS = {"**/*.md", "**/*.jsonl"}


def _is_excluded(path: Path, base: Path, exclude: tuple[str, ...]) -> bool:
    try:
        parts = path.relative_to(base).parts[:-1]
    except ValueError:
        parts = path.parts[:-1]
    blocked = {name.lower() for name in exclude}
    return any(part.lower() in blocked for part in parts)


def _expand_spec(base: Path, spec: SourceSpec) -> list[Path]:
    """Return concrete files for one spec (dirs, explicit files, or root glob)."""

    patterns = spec.patterns or DEFAULT_INCLUDE_PATTERNS
    exclude = spec.exclude or ()
    skip = {name.lower() for name in (spec.skip_files or ())}
    found: list[Path] = []
    for marker in spec.files:
        candidate = base.joinpath(*marker)
        if candidate.is_file():
            found.append(candidate)
    if spec.paths:
        for marker in spec.paths:
            directory = base.joinpath(*marker)
            if not directory.is_dir():
                continue
            for pattern in patterns:
                for path in sorted(directory.rglob(pattern)):
                    if path.name.lower() in skip:
                        continue
                    if _is_excluded(path, directory, exclude):
                        continue
                    found.append(path)
    elif not spec.files:
        for pattern in patterns:
            for path in sorted(base.glob(pattern)):
                found.append(path)
    return found


def discover_sources(
    base: Path,
    kind: SourceKind,
    *,
    source_root: SourceRoot = "project",
    config: SourcesConfig | None = None,
) -> list[DiscoveredSource]:
    """Expand the configured sources for ``kind`` under ``base``."""

    cfg = config or load_sources()
    base = base.expanduser()
    seen: set[Path] = set()
    found: list[DiscoveredSource] = []
    for spec in cfg.for_kind(kind, source_root):
        for path in _expand_spec(base, spec):
            if spec.requires_task_section and not has_task_section(path):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(DiscoveredSource(path=path, spec=spec))
    return found


def _spec_matches(directory: Path, spec: SourceSpec) -> bool:
    for marker in spec.files:
        if directory.joinpath(*marker).is_file():
            return True
    patterns = spec.patterns or DEFAULT_INCLUDE_PATTERNS
    restrictive = any(pattern not in GENERIC_PATTERNS for pattern in patterns)
    for marker in spec.paths:
        base = directory.joinpath(*marker)
        if not base.is_dir():
            continue
        if not restrictive:
            return True
        for pattern in patterns:
            if any(base.glob(pattern)):
                return True
    return False


def matching_sources(directory: Path, config: SourcesConfig | None = None) -> list[str]:
    """Return names of registration-enabled sources present in ``directory``."""

    cfg = config or load_sources()
    return [
        spec.name
        for spec in cfg.sources
        if spec.root == "project" and spec.register_project and _spec_matches(directory, spec)
    ]


def is_excluded(relative: Path | str, config: SourcesConfig | None = None) -> bool:
    """True when a path relative to the repositories root must never be a project."""

    cfg = config or load_sources()
    text = Path(relative).as_posix()
    return any(
        fnmatchcase(text, pattern) or fnmatchcase(f"{text}/", pattern)
        for pattern in cfg.discovery.exclude
    )


TASK_SECTION_KEYWORDS = (
    "roadmap",
    "milestone",
    "todo",
    "to-do",
    "backlog",
    "next steps",
    "remaining",
    "tasks",
    "task list",
    "plan",
    "этап",
    "план",
    "задач",
    "бэклог",
    "дорожная карта",
    "следующие шаги",
    "что осталось",
)

_TASK_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def heading_is_task_like(heading: str) -> bool:
    lowered = heading.lower()
    return any(keyword in lowered for keyword in TASK_SECTION_KEYWORDS)


def has_task_section(path: Path) -> bool:
    """True when a file carries a Roadmap/TODO/Backlog-like heading."""

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(heading_is_task_like(match.group(2)) for match in _TASK_HEADING.finditer(text))


_PARSERS: dict[str, Parser] = {}


def register_parser(name: str, parser: Parser) -> None:
    """Bind a parser name (used in YAML) to a parse function."""

    _PARSERS[name] = parser


def get_parser(name: str) -> Parser:
    try:
        return _PARSERS[name]
    except KeyError as exc:
        raise ValueError(f"unknown parser '{name}'; registered: {sorted(_PARSERS)}") from exc


def parse_source(path: Path, root: Path, spec: SourceSpec) -> list[Any]:
    """Parse one discovered file using the parser named in its spec."""

    return get_parser(spec.parser)(path, root, spec)
