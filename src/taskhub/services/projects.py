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
    ("docs", "backlog"),
    ("docs", "tasks"),
    ("backlog",),
    ("tasks",),
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
    return [
        "/".join(marker)
        for marker in SOURCE_MARKERS
        if directory.joinpath(*marker).is_dir()
    ]


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
        )
        if created:
            report.registered += 1
        else:
            report.existing += 1
    if not dry_run:
        await session.commit()
    return report
