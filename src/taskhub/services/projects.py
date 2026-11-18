"""Discover local project directories and register them for collection."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.repo.projects import upsert_project

SOURCE_MARKERS: tuple[tuple[str, ...], ...] = (
    ("ai", "tasks"),
    ("ai", "backlog"),
    ("ai", "analysis"),
    ("docs", "backlog"),
    ("docs", "tasks"),
    ("backlog",),
    ("tasks",),
)

# Standalone files that mark a directory as a project with a backlog/index.
SOURCE_FILE_MARKERS: tuple[tuple[str, ...], ...] = (
    ("BACKLOG.md",),
    ("backlog.md",),
    ("docs", "TASKS.md"),
)

# Folders that hold ledgers / notes even when the project has no task backlog.
NOTE_SOURCE_MARKERS: tuple[tuple[str, ...], ...] = (
    ("docs", "ledger"),
    ("docs", "notes"),
    ("docs", "sessions"),
    ("reports", "sessions"),
)

NOTE_SOURCE_FILES: tuple[tuple[str, ...], ...] = (
    ("docs", "HANDOFF.md"),
    ("HANDOFF.md",),
)

# Directories that hold projects but are not projects themselves.
CONTAINER_DIRS = {"clients", "conf", "projects", "apps", "workspaces", "services"}

# Strong markers that identify a nested project (not just any folder).
PROJECT_MARKERS = (
    "pyproject.toml",
    "package.json",
    "AGENTS.md",
    "Makefile",
    "requirements.txt",
    "docker-compose.yml",
    "Cargo.toml",
    "go.mod",
    ".git",
)

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
}


@dataclass(frozen=True)
class ProjectCandidate:
    """A directory that looks like a project with task sources."""

    slug: str
    name: str
    root_path: Path
    sources: list[str]
    description: str | None = None


@dataclass
class RegisterReport:
    """Summary of a project discovery/registration run."""

    registered: int = 0
    existing: int = 0
    projects: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "registered": self.registered,
            "existing": self.existing,
            "projects": self.projects,
        }


def slugify(value: str) -> str:
    """Turn a path fragment into a URL/id-safe project slug."""

    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "project"


def _has_sources(directory: Path) -> list[str]:
    found = [
        "/".join(marker)
        for marker in SOURCE_MARKERS
        if directory.joinpath(*marker).is_dir()
    ]
    found.extend(
        f"{'/'.join(marker)}"
        for marker in SOURCE_FILE_MARKERS
        if directory.joinpath(*marker).is_file()
    )
    found.extend(
        f"{'/'.join(marker)}"
        for marker in NOTE_SOURCE_MARKERS
        if directory.joinpath(*marker).is_dir()
    )
    found.extend(
        f"{'/'.join(marker)}"
        for marker in NOTE_SOURCE_FILES
        if directory.joinpath(*marker).is_file()
    )
    rules = directory / ".clinerules"
    if rules.is_dir() and any(rules.glob("*ledger*.md")):
        found.append(".clinerules/*ledger*.md")
    return found


def _readme_description(directory: Path) -> str | None:
    """Return the first non-trivial paragraph of the project README."""

    for name in ("README.md", "readme.md"):
        path = directory / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith(("#", ">", "|", "!", "[!"))
        ]
        for line in lines:
            if len(line) >= 20:
                return line[:500]
        return None
    return None


def _looks_like_project(directory: Path) -> bool:
    return any((directory / marker).exists() for marker in PROJECT_MARKERS)


def _is_nested_project(directory: Path, depth: int) -> bool:
    if depth == 1:
        return True
    if directory.parent.name.lower() in CONTAINER_DIRS:
        return True
    return _looks_like_project(directory)


def _walk(base: Path, depth: int, max_depth: int) -> list[tuple[Path, list[str]]]:
    found: list[tuple[Path, list[str]]] = []
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if entry.name.lower() in SKIP_DIRS:
            continue
        sources = _has_sources(entry)
        if sources and _is_nested_project(entry, depth):
            found.append((entry, sources))
        if depth < max_depth:
            found.extend(_walk(entry, depth + 1, max_depth))
    return found


def discover_projects(root: Path, *, max_depth: int = 2) -> list[ProjectCandidate]:
    """Find directories with task sources under ``root``."""

    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"root directory not found: {root}")

    candidates: list[ProjectCandidate] = []
    seen: set[Path] = set()
    for directory, sources in _walk(root, 1, max_depth):
        resolved = directory.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        relative = resolved.relative_to(root).as_posix()
        candidates.append(
            ProjectCandidate(
                slug=slugify(relative.replace("/", "-")),
                name=directory.name,
                root_path=resolved,
                sources=sources,
                description=_readme_description(directory),
            )
        )
    return sorted(candidates, key=lambda candidate: candidate.slug)


async def register_projects(
    session: AsyncSession,
    root: Path,
    *,
    max_depth: int = 2,
    dry_run: bool = False,
) -> RegisterReport:
    """Discover projects under ``root`` and upsert them into the database."""

    report = RegisterReport()
    for candidate in discover_projects(root, max_depth=max_depth):
        report.projects.append(
            {
                "slug": candidate.slug,
                "name": candidate.name,
                "root_path": str(candidate.root_path),
                "sources": ", ".join(candidate.sources),
            }
        )
        if dry_run:
            continue
        _, created = await upsert_project(
            session,
            slug=candidate.slug,
            name=candidate.name,
            root_path=str(candidate.root_path),
            description=candidate.description,
        )
        if created:
            report.registered += 1
        else:
            report.existing += 1
    if not dry_run:
        await session.commit()
    return report
