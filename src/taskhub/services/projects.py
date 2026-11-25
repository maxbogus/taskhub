"""Discover local project directories and register them for collection."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from taskhub import sources
from taskhub.models import Project
from taskhub.repo.projects import archive_project as repo_archive_project
from taskhub.repo.projects import find_by_root_path, get_project_by_slug, upsert_project
from taskhub.repo.projects import list_projects as repo_list_projects
from taskhub.repo.projects import note_signatures as repo_note_signatures
from taskhub.repo.projects import project_counts as repo_project_counts
from taskhub.repo.projects import set_root_path as repo_set_root_path

# Directories that hold projects but are not projects themselves. The list
# follows the actual portfolio layout, so a nested project is registered even
# when it has no marker file of its own.
CONTAINER_DIRS = {
    "clients",
    "conf",
    "projects",
    "apps",
    "workspaces",
    "services",
    "career",
    "hackathons",
    "legal",
    "work_projects",
    "study",
    "landings",
    "infra",
    "workshops",
    "archive",
}

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
    skipped: list[dict[str, Any]] = field(default_factory=list)
    moved: list[dict[str, Any]] = field(default_factory=list)
    archived: list[dict[str, Any]] = field(default_factory=list)
    stale: list[dict[str, Any]] = field(default_factory=list)
    duplicates: list[dict[str, Any]] = field(default_factory=list)
    detached: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "registered": self.registered,
            "existing": self.existing,
            "projects": self.projects,
            "skipped": self.skipped,
            "moved": self.moved,
            "archived": self.archived,
            "stale": self.stale,
            "duplicates": self.duplicates,
            "detached": self.detached,
        }


def _resolved(path: str | Path) -> Path:
    """Resolve a path for identity comparisons (never for display)."""

    return Path(path).expanduser().resolve()


def slugify(value: str) -> str:
    """Turn a path fragment into a URL/id-safe project slug."""

    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "project"


def _has_sources(directory: Path) -> list[str]:
    """Registrable sources present in a directory, resolved from sources.yaml."""

    return sources.matching_sources(directory)


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


def _walk(root: Path, base: Path, depth: int, max_depth: int) -> list[tuple[Path, list[str]]]:
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
        if sources.is_excluded(entry.relative_to(root)):
            continue
        matched = _has_sources(entry)
        if matched and _is_nested_project(entry, depth):
            found.append((entry, matched))
        if depth < max_depth:
            found.extend(_walk(root, entry, depth + 1, max_depth))
    return found


def discover_projects(root: Path, *, max_depth: int = 2) -> list[ProjectCandidate]:
    """Find directories with task sources under ``root``."""

    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"root directory not found: {root}")

    candidates: list[ProjectCandidate] = []
    seen: set[Path] = set()
    for directory, matched in _walk(root, root, 1, max_depth):
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
                sources=matched,
                description=_readme_description(directory),
            )
        )
    return sorted(candidates, key=lambda candidate: candidate.slug)


async def _entry(session: AsyncSession, project: Project) -> dict[str, Any]:
    """Project row plus the numbers the admin commands reason about."""

    items, notes = await repo_project_counts(session, project.id)
    return {
        "slug": project.slug,
        "name": project.name,
        "root_path": project.root_path,
        "root_exists": _resolved(project.root_path).is_dir(),
        "items": items,
        "notes": notes,
    }


def _keep_score(entry: dict[str, Any]) -> tuple[int, int, int, str]:
    """Keeper of a duplicate group: most data, then a live root, then the newest."""

    return (
        int(entry["items"]),
        int(entry["notes"]),
        1 if entry["root_exists"] else 0,
        str(entry.get("created_at") or ""),
    )


async def _duplicate_pairs(
    session: AsyncSession,
    projects: list[Project],
    *,
    roots: Mapping[str, Path] | None = None,
) -> list[tuple[list[Project], dict[str, Any]]]:
    """Groups of active projects that point at the same resolved root.

    ``roots`` overrides the root per slug, so a dry run can report the
    duplicates a planned move would create without touching the database.
    """

    override = roots or {}
    buckets: dict[Path, list[Project]] = {}
    for project in projects:
        root = override.get(project.slug) or _resolved(project.root_path)
        buckets.setdefault(root, []).append(project)
    pairs: list[tuple[list[Project], dict[str, Any]]] = []
    for root, bucket in sorted(buckets.items(), key=lambda item: str(item[0])):
        if len(bucket) < 2:
            continue
        entries = []
        for project in bucket:
            entry = await _entry(session, project)
            entry["created_at"] = project.created_at.isoformat()
            entries.append(entry)
        keeper = max(entries, key=_keep_score)
        pairs.append(
            (
                bucket,
                {
                    "root_path": str(root),
                    "keeper": keeper["slug"],
                    "slugs": sorted(str(entry["slug"]) for entry in entries),
                    "keeper_items": keeper["items"],
                    "keeper_notes": keeper["notes"],
                },
            )
        )
    return pairs


async def _root_index(
    session: AsyncSession,
    projects: list[Project],
    *,
    roots: Mapping[str, Path] | None = None,
) -> dict[Path, Project]:
    """Resolved root → project; duplicate roots resolve to the keeper.

    ``roots`` overrides the root per slug, so a dry run indexes the paths the
    planned moves will produce.
    """

    override = roots or {}
    buckets: dict[Path, list[Project]] = {}
    for project in projects:
        root = override.get(project.slug) or _resolved(project.root_path)
        buckets.setdefault(root, []).append(project)
    index: dict[Path, Project] = {}
    for root, bucket in buckets.items():
        if len(bucket) == 1:
            index[root] = bucket[0]
            continue
        entries = []
        for project in bucket:
            entry = await _entry(session, project)
            entry["created_at"] = project.created_at.isoformat()
            entries.append(entry)
        keeper_slug = max(entries, key=_keep_score)["slug"]
        index[root] = next(project for project in bucket if project.slug == keeper_slug)
    return index


async def _archive_blocker(session: AsyncSession, project: Project, keeper: Project) -> str | None:
    """Why archiving ``project`` would lose data; ``None`` means it is safe."""

    items, notes = await repo_project_counts(session, project.id)
    if items:
        return "has_work_items"
    if notes:
        own = await repo_note_signatures(session, project.id)
        covered = await repo_note_signatures(session, keeper.id)
        if not own <= covered:
            return "notes_not_covered"
    return None


async def _apply_moves(
    session: AsyncSession,
    moves: Mapping[str, str | Path],
    report: RegisterReport,
    *,
    base: Path,
    dry_run: bool,
) -> None:
    """Re-point slugs at their new checkouts before discovery runs."""

    for slug, target in moves.items():
        project = await get_project_by_slug(session, slug)
        if project is None:
            archived = await get_project_by_slug(session, slug, include_archived=True)
            reason = "archived" if archived is not None else "unknown_slug"
            report.skipped.append({"slug": slug, "reason": reason})
            continue
        raw = Path(target).expanduser()
        target_path = _resolved(raw if raw.is_absolute() else base / raw)
        if not target_path.is_dir():
            report.skipped.append(
                {"slug": slug, "target": str(target_path), "reason": "path_not_found"}
            )
            continue
        if _resolved(project.root_path) == target_path:
            continue
        owner = await find_by_root_path(session, str(target_path))
        if owner is not None and owner.id != project.id:
            owner_items, _ = await repo_project_counts(session, owner.id)
            if owner_items:
                report.skipped.append(
                    {
                        "slug": slug,
                        "target": str(target_path),
                        "reason": "target_has_work_items",
                        "keeper": owner.slug,
                    }
                )
                continue
            # An empty twin sits on the target: the move wins and the duplicate
            # pass archives the twin.
        report.moved.append(
            {
                "slug": slug,
                "from": project.root_path,
                "to": str(target_path),
                "reason": "explicit",
            }
        )
        if not dry_run:
            await repo_set_root_path(session, project, str(target_path))


async def _mark_stale(
    session: AsyncSession,
    report: RegisterReport,
    *,
    archive_missing: bool,
    dry_run: bool,
) -> None:
    """Report projects whose root is gone; flag them detached or archive them."""

    for project in await repo_list_projects(session):
        if _resolved(project.root_path).is_dir():
            if project.config.get("detached") and not dry_run:
                project.config = {
                    key: value for key, value in project.config.items() if key != "detached"
                }
            continue
        entry = await _entry(session, project)
        report.stale.append(entry)
        if archive_missing:
            reason = (
                "has_work_items" if entry["items"] else ("has_notes" if entry["notes"] else None)
            )
            if reason:
                report.skipped.append({"slug": project.slug, "reason": reason})
                continue
            report.archived.append(
                {"slug": project.slug, "reason": "missing_root", "dry_run": dry_run}
            )
            if not dry_run:
                await repo_archive_project(session, project)
            continue
        report.detached.append(entry)
        if not dry_run:
            project.config = {**project.config, "detached": True}


async def sync_projects(
    session: AsyncSession,
    root: Path,
    *,
    max_depth: int = 3,
    dry_run: bool = False,
    moves: Mapping[str, str | Path] | None = None,
    archive_missing: bool = False,
    archive_duplicates: bool = False,
) -> RegisterReport:
    """Bring the project registry in line with the folders on disk.

    Identity is the resolved ``root_path``: a known path never produces a
    second slug, and ``moves`` re-points a slug at a new checkout without
    touching work items. Archiving is soft and refuses to hide data.
    """

    base = _resolved(root)
    if not base.is_dir():
        raise ValueError(f"root directory not found: {base}")

    report = RegisterReport()
    if moves:
        await _apply_moves(session, moves, report, base=base, dry_run=dry_run)
        if not dry_run:
            await session.flush()

    candidates = discover_projects(base, max_depth=max_depth)
    projected = {entry["slug"]: _resolved(entry["to"]) for entry in report.moved}
    active = await repo_list_projects(session)
    index = await _root_index(session, active, roots=projected)
    by_slug = {project.slug: project for project in active}

    for candidate in candidates:
        report.projects.append(
            {
                "slug": candidate.slug,
                "name": candidate.name,
                "root_path": str(candidate.root_path),
                "sources": ", ".join(candidate.sources),
            }
        )
        resolved = candidate.root_path
        keeper = index.get(resolved)
        if keeper is not None:
            report.existing += 1
            if keeper.slug != candidate.slug:
                report.skipped.append(
                    {
                        "slug": candidate.slug,
                        "root_path": str(resolved),
                        "reason": "already_registered",
                        "keeper": keeper.slug,
                    }
                )
                continue
            if not dry_run:
                keeper.name = candidate.name
                if candidate.description is not None:
                    keeper.description = candidate.description
                if keeper.config.get("detached"):
                    keeper.config = {
                        key: value for key, value in keeper.config.items() if key != "detached"
                    }
                await session.flush()
            continue

        existing = by_slug.get(candidate.slug)
        if existing is not None:
            report.existing += 1
            report.moved.append(
                {
                    "slug": existing.slug,
                    "from": existing.root_path,
                    "to": str(resolved),
                    "reason": "slug",
                }
            )
            if not dry_run:
                await repo_set_root_path(session, existing, str(resolved))
            index[resolved] = existing
            continue

        if dry_run:
            report.registered += 1
            continue
        project, created = await upsert_project(
            session,
            slug=candidate.slug,
            name=candidate.name,
            root_path=str(resolved),
            description=candidate.description,
        )
        index[resolved] = project
        by_slug[project.slug] = project
        if created:
            report.registered += 1
        else:
            report.existing += 1

    if not dry_run:
        await session.flush()

    for bucket, payload in await _duplicate_pairs(
        session, await repo_list_projects(session), roots=projected
    ):
        report.duplicates.append(payload)
        if not archive_duplicates:
            continue
        keeper = next(project for project in bucket if project.slug == payload["keeper"])
        for project in bucket:
            if project.slug == keeper.slug:
                continue
            reason = await _archive_blocker(session, project, keeper)
            if reason:
                report.skipped.append(
                    {"slug": project.slug, "reason": reason, "keeper": keeper.slug}
                )
                continue
            report.archived.append(
                {
                    "slug": project.slug,
                    "reason": "duplicate",
                    "keeper": keeper.slug,
                    "dry_run": dry_run,
                }
            )
            if not dry_run:
                await repo_archive_project(session, project)

    await _mark_stale(session, report, archive_missing=archive_missing, dry_run=dry_run)

    if not dry_run:
        await session.commit()
    return report


async def register_projects(
    session: AsyncSession,
    root: Path,
    *,
    max_depth: int = 2,
    dry_run: bool = False,
) -> RegisterReport:
    """Audit-only registration kept for clients of the old API."""

    return await sync_projects(session, root, max_depth=max_depth, dry_run=dry_run)


async def move_project(
    session: AsyncSession,
    slug: str,
    root_path: str | Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Re-point ``slug`` at another checkout; work items stay attached."""

    project = await get_project_by_slug(session, slug)
    if project is None:
        raise ValueError(f"project '{slug}' not found")
    target = _resolved(root_path)
    if not target.is_dir():
        raise ValueError(f"path not found: {target}")
    owner = await find_by_root_path(session, str(target))
    if owner is not None and owner.id != project.id:
        raise ValueError(f"path already registered as '{owner.slug}'")
    if _resolved(project.root_path) == target:
        return {
            "slug": slug,
            "from": project.root_path,
            "to": str(target),
            "moved": False,
            "dry_run": dry_run,
        }
    previous = project.root_path
    if not dry_run:
        await repo_set_root_path(session, project, str(target))
        await session.commit()
    return {
        "slug": slug,
        "from": previous,
        "to": str(target),
        "moved": True,
        "dry_run": dry_run,
    }


async def archive_project(
    session: AsyncSession,
    slug: str,
    *,
    force: bool = False,
    restore: bool = False,
) -> dict[str, Any]:
    """Soft-archive a project (or restore it); a project with data needs ``force``."""

    project = await get_project_by_slug(session, slug, include_archived=True)
    if project is None:
        raise ValueError(f"project '{slug}' not found")
    if restore:
        await repo_archive_project(session, project, restore=True)
        await session.commit()
        return {"slug": slug, "archived": False, "restored": True}
    if project.archived_at is not None:
        return {"slug": slug, "archived": True, "restored": False, "already": True}
    items, notes = await repo_project_counts(session, project.id)
    if items and not force:
        raise ValueError(
            f"project '{slug}' has {items} work items; pass force=True to archive anyway"
        )
    await repo_archive_project(session, project)
    await session.commit()
    return {
        "slug": slug,
        "archived": True,
        "restored": False,
        "work_items": items,
        "notes": notes,
    }


@dataclass
class AuditReport:
    """Health of the project registry against the folders on disk."""

    root: str
    total: int = 0
    active: int = 0
    live: int = 0
    archived: int = 0
    stale: list[dict[str, Any]] = field(default_factory=list)
    duplicates: list[dict[str, Any]] = field(default_factory=list)
    archived_projects: list[dict[str, Any]] = field(default_factory=list)
    uncovered: list[dict[str, Any]] = field(default_factory=list)
    orphan_items: int = 0
    orphan_notes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "total": self.total,
            "active": self.active,
            "live": self.live,
            "archived": self.archived,
            "stale": self.stale,
            "duplicates": self.duplicates,
            "archived_projects": self.archived_projects,
            "uncovered": self.uncovered,
            "orphan_items": self.orphan_items,
            "orphan_notes": self.orphan_notes,
        }

    def to_markdown(self) -> str:
        """Render the audit as the collection map the agents read."""

        lines = [
            "# Project audit",
            "",
            f"- root: `{self.root}`",
            f"- projects: {self.total} total, {self.active} active, {self.live} live, "
            f"{self.archived} archived",
            f"- orphaned by missing roots: {self.orphan_items} work items, "
            f"{self.orphan_notes} notes",
            "",
            "## Stale projects (root missing)",
            "",
            "| slug | root_path | items | notes |",
            "|---|---|---|---|",
        ]
        for entry in self.stale:
            lines.append(
                f"| {entry['slug']} | `{entry['root_path']}` | {entry['items']} | "
                f"{entry['notes']} |"
            )
        lines += [
            "",
            "## Duplicate registrations (same root)",
            "",
            "| root_path | keeper | slugs |",
            "|---|---|---|",
        ]
        for entry in self.duplicates:
            lines.append(
                f"| `{entry['root_path']}` | {entry['keeper']} | {', '.join(entry['slugs'])} |"
            )
        lines += [
            "",
            "## Uncovered directories",
            "",
            "| path | inside | sources |",
            "|---|---|---|",
        ]
        for entry in self.uncovered:
            lines.append(f"| `{entry['path']}` | {entry['inside'] or '—'} | {entry['sources']} |")
        lines += [
            "",
            "## Archived projects",
            "",
            "| slug | root_path | items | notes |",
            "|---|---|---|---|",
        ]
        for entry in self.archived_projects:
            lines.append(
                f"| {entry['slug']} | `{entry['root_path']}` | {entry['items']} | "
                f"{entry['notes']} |"
            )
        return "\n".join(lines) + "\n"


async def audit_projects(
    session: AsyncSession,
    root: Path,
    *,
    max_depth: int = 3,
) -> AuditReport:
    """Audit the registry against the folders on disk (read-only)."""

    base = _resolved(root)
    if not base.is_dir():
        raise ValueError(f"root directory not found: {base}")

    projects = await repo_list_projects(session, include_archived=True)
    active = [project for project in projects if project.archived_at is None]
    archived = [project for project in projects if project.archived_at is not None]
    report = AuditReport(
        root=str(base), total=len(projects), active=len(active), archived=len(archived)
    )

    roots: dict[Path, list[Project]] = {}
    for project in active:
        roots.setdefault(_resolved(project.root_path), []).append(project)

    for project in active:
        entry = await _entry(session, project)
        if entry["root_exists"]:
            continue
        report.stale.append(entry)
        report.orphan_items += int(entry["items"])
        report.orphan_notes += int(entry["notes"])
    report.live = report.active - len(report.stale)

    for project in archived:
        report.archived_projects.append(await _entry(session, project))

    report.duplicates = [payload for _, payload in await _duplicate_pairs(session, active)]

    known = {_resolved(project.root_path) for project in active}
    for candidate in discover_projects(base, max_depth=max_depth):
        if candidate.root_path in known:
            continue
        inside: str | None = None
        for root_path, bucket in sorted(
            roots.items(), key=lambda item: len(str(item[0])), reverse=True
        ):
            if root_path in candidate.root_path.parents:
                inside = bucket[0].slug
                break
        report.uncovered.append(
            {
                "slug": candidate.slug,
                "path": str(candidate.root_path),
                "sources": ", ".join(candidate.sources),
                "inside": inside,
            }
        )
    return report
