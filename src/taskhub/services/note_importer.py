"""Import ledgers and notes from project files into TaskHub (notes tracker).

Sources (project-local):

* ``docs/ledger/*.jsonl`` — decisions / findings / submissions / benchmarks.
* ``docs/HANDOFF.md`` or ``HANDOFF.md`` — session handoff notes.
* ``docs/notes/*.md`` — human-readable notes.
* ``docs/sessions/*.md`` and ``reports/sessions/*.md`` — session journals.
* ``.clinerules/*-ledger*.md`` — ledger format rules (meta ``type=rule``).

Global memory (pseudo-project ``memory``):

* ``~/.cline/memory/*.jsonl`` — cross-project session / ML progress memory.

Notes are upserted idempotently by ``(project_id, external_ref)`` and linked to
work items by matching task ids (``tickets`` fields or ``[A-Z]+-<n>`` tokens)
against the project's imported work items.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub import sources
from taskhub.config import get_settings
from taskhub.models import Note, NoteKind, Project, WorkItem
from taskhub.repo.projects import upsert_project
from taskhub.services.backup import create_backup

_TASK_ID_RE = re.compile(r"\b([A-Z][A-Z0-9_.-]{0,30}-\d+)\b")
_JSONL_SUFFIXES = {".jsonl"}


@dataclass
class ParsedNote:
    """A normalised note extracted from one source entry."""

    external_key: str
    kind: NoteKind
    content: str
    source_path: Path
    ledger_type: str = "ledger"
    task_ids: list[str] = field(default_factory=list)
    author: str | None = None
    created_at: datetime | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class NoteImportReport:
    """Per-project results for a note import run."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    linked: int = 0
    errors: list[str] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "linked": self.linked,
            "errors": self.errors,
            "items": self.items,
        }


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _scan_task_ids(text: Any) -> list[str]:
    """Return unique task-id tokens found in a string; ignore other types."""

    if not isinstance(text, str):
        return []
    found: list[str] = []
    for match in _TASK_ID_RE.finditer(text):
        value = match.group(1)
        if value not in found:
            found.append(value)
    return found


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _kind_for_name(name: str) -> tuple[NoteKind, str]:
    """Map a source filename to a note kind and ledger type."""

    lowered = name.lower()
    if "decision" in lowered:
        return NoteKind.decision, "decision"
    if "finding" in lowered:
        return NoteKind.insight, "finding"
    if "submission" in lowered:
        return NoteKind.ledger, "submission"
    if "benchmark" in lowered:
        return NoteKind.ledger, "benchmark"
    if "session" in lowered:
        return NoteKind.ledger, "session"
    if "handoff" in lowered:
        return NoteKind.handoff, "handoff"
    if "ledger" in lowered and lowered.endswith(".md"):
        return NoteKind.ledger, "rule"
    return NoteKind.ledger, "ledger"


def _ledger_content(data: dict[str, Any]) -> str:
    """Render a JSONL ledger record into readable note text."""

    title = (
        data.get("title")
        or data.get("topic")
        or data.get("task")
        or data.get("run_name")
        or data.get("run_id")
        or data.get("id")
    )
    parts: list[str] = []
    if title:
        parts.append(str(title))
    for key in ("context", "decision", "reason", "value", "note", "evidence", "status"):
        value = data.get(key)
        if value:
            parts.append(f"{key}: {value}")
    metrics = data.get("metrics")
    if metrics:
        parts.append(f"metrics: {json.dumps(metrics, ensure_ascii=False)}")
    tags = data.get("tags")
    if tags:
        rendered = ", ".join(map(str, tags)) if isinstance(tags, list) else str(tags)
        parts.append(f"tags: {rendered}")
    if not parts:
        parts.append(json.dumps(data, ensure_ascii=False))
    return "\n".join(parts)[:20000]


def _task_ids_from_data(data: dict[str, Any]) -> list[str]:
    """Collect task ids from ``tickets`` plus free-text fields."""

    found: list[str] = []
    tickets = data.get("tickets")
    candidates: list[str] = []
    if isinstance(tickets, list):
        candidates.extend(str(item) for item in tickets)
    elif isinstance(tickets, str):
        candidates.extend(re.split(r"[,;\s]+", tickets))
    for value in candidates:
        value = value.strip()
        if value and value not in found:
            found.append(value)
    for key in ("title", "context", "decision", "reason", "value", "note", "evidence"):
        for task_id in _scan_task_ids(data.get(key)):
            if task_id not in found:
                found.append(task_id)
    return found


def _resolve_ledger_kind(filename: str, spec: sources.SourceSpec | None) -> tuple[NoteKind, str]:
    """Resolve note kind / ledger type from the spec, falling back to filename."""

    derived_kind, derived_type = _kind_for_name(filename)
    if spec is None:
        return derived_kind, derived_type
    kind = derived_kind if spec.note_kind in (None, "auto") else NoteKind(spec.note_kind)
    return kind, spec.ledger_type or derived_type


def parse_jsonl(path: Path, root: Path, spec: sources.SourceSpec | None = None) -> list[ParsedNote]:
    """Parse a JSONL ledger; each non-empty line becomes one note."""

    notes: list[ParsedNote] = []
    kind, ledger_type = _resolve_ledger_kind(path.name, spec)
    scope = spec.scope if spec is not None else "project"
    text = path.read_text(encoding="utf-8", errors="replace")
    for index, raw in enumerate(text.splitlines()):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        key = str(
            data.get("id") or data.get("run_id") or data.get("run_name") or f"line-{index + 1}"
        )
        meta: dict[str, Any] = {
            "source": _relative(path, root),
            "ledger_type": ledger_type,
            "raw": data,
        }
        if scope == "global":
            meta["scope"] = "global"
        notes.append(
            ParsedNote(
                external_key=key,
                kind=kind,
                content=_ledger_content(data),
                source_path=path,
                ledger_type=ledger_type,
                task_ids=_task_ids_from_data(data),
                created_at=_parse_ts(data.get("ts")),
                meta=meta,
            )
        )
    return notes


def parse_markdown(
    path: Path,
    root: Path,
    kind: NoteKind,
    ledger_type: str,
    scope: str = "project",
) -> list[ParsedNote]:
    """Parse a markdown note/handoff/session file into a single note."""

    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return []
    meta: dict[str, Any] = {
        "source": _relative(path, root),
        "ledger_type": ledger_type,
    }
    if scope == "global":
        meta["scope"] = "global"
    return [
        ParsedNote(
            external_key="file",
            kind=kind,
            content=text[:20000],
            source_path=path,
            ledger_type=ledger_type,
            task_ids=_scan_task_ids(text),
            meta=meta,
        )
    ]


def discover_note_sources(root: Path) -> list[Path]:
    """Backward-compatible list of note source paths under a project."""

    return [item.path for item in sources.discover_sources(root, "note")]


def _explicit_note_spec(path: Path) -> sources.SourceSpec:
    parser = "jsonl_ledger" if path.suffix.lower() in _JSONL_SUFFIXES else "markdown_note"
    return sources.SourceSpec(name="explicit", kind="note", parser=parser)


def _parse_note_discovered(item: sources.DiscoveredSource, root: Path) -> list[ParsedNote]:
    return cast(list[ParsedNote], sources.parse_source(item.path, root, item.spec))


async def _build_task_lookup(session: AsyncSession, project_id: str | None) -> dict[str, str]:
    """Map external task ids to work-item ids.

    For a single project every match is trusted; for a global lookup
    (``project_id is None``) only unambiguous ids are kept.
    """

    stmt = select(WorkItem)
    if project_id is not None:
        stmt = stmt.where(WorkItem.project_id == project_id)
    rows = list((await session.execute(stmt)).scalars().all())
    candidates: dict[str, list[str]] = {}
    for item in rows:
        keys: set[str] = set()
        external_id = (item.meta or {}).get("external_id")
        if external_id:
            keys.add(str(external_id))
        if item.external_ref:
            keys.add(item.external_ref.rsplit(":", 1)[-1])
        for key in keys:
            candidates.setdefault(key, []).append(item.id)

    lookup: dict[str, str] = {}
    for key, ids in candidates.items():
        if project_id is not None or len(set(ids)) == 1:
            lookup[key] = ids[0]
    return lookup


async def _apply_parsed(
    session: AsyncSession,
    project: Project,
    root: Path,
    parsed: list[ParsedNote],
    lookup: dict[str, str],
    report: NoteImportReport,
    dry_run: bool,
) -> None:
    existing_rows = list(
        (
            await session.execute(
                select(Note).where(Note.project_id == project.id, Note.external_ref.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    existing = {note.external_ref: note for note in existing_rows}
    # Deduplicate by external_ref within this run (duplicate ledger ids): last wins.
    deduped: dict[str, ParsedNote] = {}
    for pnote in parsed:
        ref = f"{_relative(pnote.source_path, root)}:{pnote.external_key}"
        deduped[ref] = pnote
    for ref, pnote in deduped.items():
        work_item_id = next(
            (lookup[task_id] for task_id in pnote.task_ids if task_id in lookup),
            None,
        )
        if work_item_id:
            report.linked += 1
        meta = dict(pnote.meta)
        note = existing.get(ref)
        if note is None:
            if dry_run:
                report.created += 1
                report.items.append(
                    {"external_ref": ref, "kind": pnote.kind.value, "action": "created"}
                )
                continue
            note = Note(
                project_id=project.id,
                kind=pnote.kind,
                content=pnote.content,
                author=pnote.author,
                external_ref=ref,
                work_item_id=work_item_id,
                meta=meta,
            )
            if pnote.created_at is not None:
                note.created_at = pnote.created_at
            session.add(note)
            existing[ref] = note
            report.created += 1
            action = "created"
        else:
            changed = False
            if note.content != pnote.content:
                note.content = pnote.content
                changed = True
            if note.kind != pnote.kind:
                note.kind = pnote.kind
                changed = True
            if note.work_item_id != work_item_id:
                note.work_item_id = work_item_id
                changed = True
            if note.meta != meta:
                note.meta = meta
                changed = True
            if changed:
                report.updated += 1
                action = "updated"
            else:
                report.skipped += 1
                action = "skipped"
        report.items.append({"external_ref": ref, "kind": pnote.kind.value, "action": action})


async def import_notes(
    session: AsyncSession,
    project: Project,
    *,
    source_path: Path | None = None,
    dry_run: bool = False,
    skip_backup: bool = False,
    backup_dir: Path | None = None,
) -> NoteImportReport:
    """Import ledgers / notes for one project (idempotent upsert)."""

    report = NoteImportReport()
    root = Path(project.root_path).expanduser()
    if source_path is not None:
        source_file = Path(source_path)
        discovered = [
            sources.DiscoveredSource(path=source_file, spec=_explicit_note_spec(source_file))
        ]
    else:
        discovered = sources.discover_sources(root, "note")

    parsed: list[ParsedNote] = []
    for item in discovered:
        try:
            parsed.extend(_parse_note_discovered(item, root))
        except Exception as exc:  # one bad file must not abort the project
            report.errors.append(f"{item.path}: {exc}")

    if not dry_run and not skip_backup:
        await create_backup(backup_dir=backup_dir)

    lookup = await _build_task_lookup(session, project.id)
    await _apply_parsed(session, project, root, parsed, lookup, report, dry_run)
    if not dry_run:
        await session.flush()
        await session.commit()
    return report


async def import_memory(
    session: AsyncSession,
    *,
    memory_root: Path | None = None,
    dry_run: bool = False,
    skip_backup: bool = False,
    backup_dir: Path | None = None,
) -> NoteImportReport:
    """Import ``~/.cline/memory/*.jsonl`` into the ``memory`` pseudo-project."""

    report = NoteImportReport()
    root = Path(memory_root).expanduser() if memory_root is not None else get_settings().memory_root
    if not root.is_dir():
        report.errors.append(f"memory root not found: {root}")
        return report

    if not dry_run and not skip_backup:
        await create_backup(backup_dir=backup_dir)

    project, _ = await upsert_project(
        session,
        slug="memory",
        name="Cline memory",
        root_path=str(root),
        description="Global Cline session memory (sessions + ML progress)",
    )
    parsed: list[ParsedNote] = []
    for item in sources.discover_sources(root, "note", source_root="memory"):
        try:
            parsed.extend(_parse_note_discovered(item, root))
        except Exception as exc:
            report.errors.append(f"{item.path}: {exc}")

    lookup = await _build_task_lookup(session, None)
    await _apply_parsed(session, project, root, parsed, lookup, report, dry_run)
    if not dry_run:
        await session.flush()
        await session.commit()
    return report


def _merge(target: NoteImportReport, source: NoteImportReport) -> NoteImportReport:
    target.created += source.created
    target.updated += source.updated
    target.skipped += source.skipped
    target.linked += source.linked
    target.errors.extend(source.errors)
    target.items.extend(source.items)
    return target


async def import_all_notes(
    session: AsyncSession,
    projects: list[Project],
    *,
    memory_root: Path | None = None,
    include_memory: bool = True,
    dry_run: bool = False,
    skip_backup: bool = False,
    backup_dir: Path | None = None,
) -> dict[str, Any]:
    """Import notes for every project plus the global memory pseudo-project."""

    totals = NoteImportReport()
    per_project: list[dict[str, Any]] = []

    if not dry_run and not skip_backup:
        await create_backup(backup_dir=backup_dir)

    for project in projects:
        try:
            result = await import_notes(
                session,
                project,
                dry_run=dry_run,
                skip_backup=True,
                backup_dir=backup_dir,
            )
        except Exception as exc:
            per_project.append({"slug": project.slug, "error": f"{type(exc).__name__}: {exc}"})
            continue
        _merge(totals, result)
        per_project.append(
            {
                "slug": project.slug,
                "created": result.created,
                "updated": result.updated,
                "skipped": result.skipped,
                "linked": result.linked,
                "errors": len(result.errors),
            }
        )

    if include_memory:
        try:
            memory_report = await import_memory(
                session,
                memory_root=memory_root,
                dry_run=dry_run,
                skip_backup=True,
                backup_dir=backup_dir,
            )
            _merge(totals, memory_report)
            per_project.append(
                {
                    "slug": "memory",
                    "created": memory_report.created,
                    "updated": memory_report.updated,
                    "skipped": memory_report.skipped,
                    "linked": memory_report.linked,
                    "errors": len(memory_report.errors),
                }
            )
        except Exception as exc:
            per_project.append({"slug": "memory", "error": f"{type(exc).__name__}: {exc}"})

    return {"totals": totals.to_dict(), "projects": per_project, "dry_run": dry_run}


# --------------------------------------------------------------- parsers
# Bind parser names (referenced from sources.yaml) to concrete functions.


def _parser_jsonl_ledger(path: Path, root: Path, spec: sources.SourceSpec) -> list[ParsedNote]:
    return parse_jsonl(path, root, spec)


def _parser_markdown_note(path: Path, root: Path, spec: sources.SourceSpec) -> list[ParsedNote]:
    if spec.note_kind and spec.note_kind != "auto":
        kind = NoteKind(spec.note_kind)
    else:
        kind = _kind_for_name(path.name)[0]
    ledger_type = spec.ledger_type or _kind_for_name(path.name)[1]
    return parse_markdown(path, root, kind, ledger_type, spec.scope)


sources.register_parser("jsonl_ledger", _parser_jsonl_ledger)
sources.register_parser("markdown_note", _parser_markdown_note)
