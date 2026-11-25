"""Bulk backlog collection across all registered projects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Project
from taskhub.services.backlog_importer import import_backlog
from taskhub.services.backup import create_backup
from taskhub.services.note_importer import import_all_notes


@dataclass
class CollectReport:
    """Per-project results plus totals for a bulk import run."""

    projects: list[dict[str, Any]] = field(default_factory=list)
    totals: dict[str, int] = field(
        default_factory=lambda: {
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
            "detached": 0,
        }
    )
    notes: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"totals": self.totals, "projects": self.projects}
        if self.notes is not None:
            payload["notes"] = self.notes
        return payload


async def import_all(
    session: AsyncSession,
    projects: list[Project],
    *,
    dry_run: bool = False,
    skip_backup: bool = False,
    backup_dir: Path | None = None,
    include_notes: bool = False,
) -> CollectReport:
    """Import backlogs for every project; one backup covers the whole run."""

    report = CollectReport()
    if not dry_run and not skip_backup:
        await create_backup(backup_dir=backup_dir)

    for project in projects:
        if not Path(project.root_path).expanduser().is_dir():
            # A detached project keeps its history; importing into a missing
            # root would silently do nothing, so it is reported instead.
            report.projects.append(
                {
                    "slug": project.slug,
                    "detached": True,
                    "root_path": project.root_path,
                }
            )
            report.totals["detached"] += 1
            continue
        try:
            result = await import_backlog(
                session,
                project,
                dry_run=dry_run,
                skip_backup=True,
                backup_dir=backup_dir,
            )
        except Exception as exc:  # one bad project must not abort the run
            report.projects.append({"slug": project.slug, "error": f"{type(exc).__name__}: {exc}"})
            report.totals["errors"] += 1
            continue

        payload = result.to_dict()
        entry = {
            "slug": project.slug,
            "created": payload["created"],
            "updated": payload["updated"],
            "skipped": payload["skipped"],
            "errors": len(payload["errors"]),
        }
        if payload["errors"]:
            entry["messages"] = payload["errors"][:10]
        report.projects.append(entry)
        for key in ("created", "updated", "skipped"):
            report.totals[key] += int(payload[key])
        report.totals["errors"] += len(payload["errors"])

    if include_notes:
        report.notes = await import_all_notes(
            session,
            projects,
            dry_run=dry_run,
            skip_backup=True,
            backup_dir=backup_dir,
        )

    return report
