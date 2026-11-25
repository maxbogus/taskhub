"""MCP tools for TaskHub (M1 subset)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from taskhub.config import get_settings
from taskhub.db import session_scope
from taskhub.models import (
    Agent,
    AgentRole,
    Claim,
    DocType,
    Document,
    ModelPrice,
    Note,
    NoteKind,
    Project,
    TaskTemplate,
    WorkItem,
    WorkItemType,
    WorkStatus,
)
from taskhub.repo.agents import get_or_create_agent as repo_get_or_create_agent
from taskhub.repo.claims import list_active_claims as repo_list_active_claims
from taskhub.repo.documents import get_document as repo_get_document
from taskhub.repo.documents import list_documents as repo_list_documents
from taskhub.repo.notes import add_note as repo_add_note
from taskhub.repo.notes import search_notes as repo_search_notes
from taskhub.repo.projects import get_project_by_slug
from taskhub.repo.projects import list_projects as repo_list_projects
from taskhub.repo.templates import get_template as repo_get_template
from taskhub.repo.templates import list_templates as repo_list_templates
from taskhub.repo.work_items import (
    create_item,
    get_item,
    next_item,
)
from taskhub.repo.work_items import (
    list_items as repo_list_items,
)
from taskhub.services.backlog_importer import import_backlog as import_backlog_service
from taskhub.services.backup import create_backup
from taskhub.services.claims import (
    ClaimError,
)
from taskhub.services.claims import (
    acquire_claim as acquire_claim_service,
)
from taskhub.services.claims import (
    release_claim as release_claim_service,
)
from taskhub.services.collect import import_all as import_all_service
from taskhub.services.documents import upsert_document as upsert_document_service
from taskhub.services.economics import (
    cost_series as cost_series_service,
)
from taskhub.services.economics import (
    economics_report as economics_report_service,
)
from taskhub.services.economics import (
    economics_summary as economics_summary_service,
)
from taskhub.services.economics import (
    load_price_specs,
)
from taskhub.services.evaluation import evaluate_project as evaluate_project_service
from taskhub.services.export import export_notes as export_notes_service
from taskhub.services.gates import (
    FlowError,
    blocking_items,
)
from taskhub.services.gates import (
    change_status as change_status_service,
)
from taskhub.services.gates import (
    workflow_info as workflow_info_service,
)
from taskhub.services.metric_ingest import (
    ingest_cline_tasks,
)
from taskhub.services.metric_ingest import (
    log_activity as log_activity_service,
)
from taskhub.services.metric_ingest import (
    metrics_report as metrics_report_service,
)
from taskhub.services.metric_ingest import (
    task_metrics as task_metrics_service,
)
from taskhub.services.note_importer import import_all_notes as import_all_notes_service
from taskhub.services.portfolio import portfolio_summary as portfolio_summary_service
from taskhub.services.price_store import (
    add_audit_note,
)
from taskhub.services.price_store import (
    close_price as close_price_service,
)
from taskhub.services.price_store import (
    set_price as set_price_service,
)
from taskhub.services.price_store import (
    set_project_rates as set_project_rates_service,
)
from taskhub.services.price_store import (
    sync_prices as sync_prices_service,
)
from taskhub.services.projects import archive_project as archive_project_service
from taskhub.services.projects import audit_projects as audit_projects_service
from taskhub.services.projects import move_project as move_project_service
from taskhub.services.projects import register_projects as register_projects_service
from taskhub.services.projects import sync_projects as sync_projects_service
from taskhub.services.scope import resolve_scope
from taskhub.services.templates import (
    DEFAULT_TEMPLATE_SOURCES,
)
from taskhub.services.templates import (
    apply_template as apply_template_service,
)
from taskhub.services.templates import (
    import_templates as import_templates_service,
)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _work_item_type(value: str) -> WorkItemType:
    try:
        return WorkItemType(value)
    except ValueError as exc:
        raise ValueError(f"unknown work item type: {value}") from exc


def _ref(item: WorkItem | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {"id": item.id, "title": item.title, "status": item.status.value}


def serialize_item(item: WorkItem, *, compact: bool = True) -> dict[str, Any]:
    """Serialise an item using a compact shape by default."""

    data: dict[str, Any] = {
        "id": item.id,
        "project_id": item.project_id,
        "type": item.type.value,
        "title": item.title,
        "status": item.status.value,
        "priority": item.priority,
        "order_index": item.order_index,
        "estimate_hours": item.estimate_hours,
        "due_at": item.due_at.isoformat() if item.due_at else None,
        "required_skill": item.required_skill,
        "external_ref": item.external_ref,
        "parent_id": item.parent_id,
        "meta": item.meta or {},
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "done_at": item.done_at.isoformat() if item.done_at else None,
    }
    if not compact:
        data["description"] = item.description
        data["acceptance_criteria"] = item.acceptance_criteria
    return data


async def create_task(
    project_slug: str,
    title: str,
    type: str = "task",
    description: str | None = None,
    parent_id: str | None = None,
    priority: int = 3,
    estimate_hours: float | None = None,
    required_skill: str | None = None,
    due_at: str | None = None,
    external_ref: str | None = None,
) -> dict[str, Any]:
    """Create a work item in a project."""

    async with session_scope() as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found")
        item = await create_item(
            session,
            project_id=project.id,
            title=title,
            type=_work_item_type(type),
            description=description,
            parent_id=parent_id,
            priority=priority,
            estimate_hours=estimate_hours,
            due_at=_parse_dt(due_at),
            required_skill=required_skill,
            external_ref=external_ref,
        )
        await session.commit()
        return serialize_item(item, compact=False)


async def get_context(item_id: str, note_limit: int = 5) -> dict[str, Any]:
    """Return compact task context: parent, children, blockers, recent notes."""

    async with session_scope() as session:
        item = await get_item(session, item_id)
        if item is None:
            raise ValueError(f"work item '{item_id}' not found")
        project = await session.get(Project, item.project_id)
        parent = await get_item(session, item.parent_id) if item.parent_id else None
        children = list(
            (
                await session.execute(
                    select(WorkItem)
                    .where(WorkItem.parent_id == item.id)
                    .order_by(WorkItem.priority.asc())
                )
            )
            .scalars()
            .all()
        )
        blockers = await blocking_items(session, item.id)
        notes = list(
            (
                await session.execute(
                    select(Note)
                    .where(Note.work_item_id == item.id)
                    .order_by(Note.created_at.desc())
                    .limit(note_limit)
                )
            )
            .scalars()
            .all()
        )
        return {
            "item": serialize_item(item),
            "parent": _ref(parent),
            "children": [_ref(child) for child in children],
            "blocking": blockers,
            "last_notes": [
                {
                    "kind": note.kind.value,
                    "content": note.content[:500],
                    "author": note.author,
                    "created_at": note.created_at.isoformat(),
                }
                for note in notes
            ],
            "links": {
                "task": f"task://{item.id}",
                "context": f"task://{item.id}/context",
                "backlog": f"project://{project.slug}/backlog" if project else None,
            },
        }


async def get_next_item(
    project_slug: str | None = None,
    cwd: str | None = None,
    required_skill: str | None = None,
) -> dict[str, Any]:
    """Return the highest-priority available task for the resolved scope."""

    async with session_scope() as session:
        scope = await resolve_scope(session, cwd=cwd, project_slug=project_slug)
        if scope.project is None:
            return {"item": None, "reason": "global scope: pass project_slug or cwd"}
        item = await next_item(session, scope.project.id, required_skill=required_skill)
        if item is None:
            return {
                "item": None,
                "reason": "no available items",
                "project": scope.project.slug,
            }
        return {"item": serialize_item(item), "project": scope.project.slug}


async def update_task(
    item_id: str,
    title: str | None = None,
    description: str | None = None,
    priority: int | None = None,
    estimate_hours: float | None = None,
    due_at: str | None = None,
    required_skill: str | None = None,
    acceptance_criteria: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update mutable fields; status changes must go through ``change_status``."""

    async with session_scope() as session:
        item = await get_item(session, item_id)
        if item is None:
            raise ValueError(f"work item '{item_id}' not found")
        if title is not None:
            item.title = title
        if description is not None:
            item.description = description
        if priority is not None:
            item.priority = priority
        if estimate_hours is not None:
            item.estimate_hours = estimate_hours
        if due_at is not None:
            item.due_at = _parse_dt(due_at)
        if required_skill is not None:
            item.required_skill = required_skill
        if acceptance_criteria is not None:
            item.acceptance_criteria = acceptance_criteria
        if meta is not None:
            merged = dict(item.meta or {})
            merged.update(meta)
            item.meta = merged
        await session.commit()
        return serialize_item(item, compact=False)


async def change_status(
    item_id: str,
    target_status: str,
    role: str = "agent",
    actor: str = "mcp-client",
    note: str | None = None,
) -> dict[str, Any]:
    """Move a work item through the role-aware workflow with gates."""

    async with session_scope() as session:
        try:
            result = await change_status_service(
                session,
                item_id,
                target_status,
                role=role,
                actor=actor,
                note=note,
            )
            await session.commit()
        except FlowError as exc:
            raise ValueError(str(exc)) from exc
        return {
            "item_id": result.item_id,
            "from_status": result.from_status,
            "to_status": result.to_status,
            "note_id": result.note_id,
        }


async def get_workflow(item_id: str, role: str = "agent") -> dict[str, Any]:
    """Return current status, allowed targets and gate errors."""

    async with session_scope() as session:
        try:
            return await workflow_info_service(session, item_id, role)
        except FlowError as exc:
            raise ValueError(str(exc)) from exc


async def list_projects() -> list[dict[str, Any]]:
    """Return the global project map with per-status counters."""

    async with session_scope() as session:
        projects = await repo_list_projects(session)
        result: list[dict[str, Any]] = []
        for project in projects:
            rows = (
                await session.execute(
                    select(WorkItem.status, func.count())
                    .where(WorkItem.project_id == project.id)
                    .group_by(WorkItem.status)
                )
            ).all()
            result.append(
                {
                    "slug": project.slug,
                    "name": project.name,
                    "root_path": project.root_path,
                    "by_status": {status.value: count for status, count in rows},
                }
            )
        return result


async def project_status(project_slug: str) -> dict[str, Any]:
    """Compact project snapshot: counters, EVM and a short forecast."""

    async with session_scope() as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found")
        evaluation = await evaluate_project_service(
            session, project, n_iterations=1000, random_seed=42
        )
        forecast = evaluation["forecast"]
        return {
            "project": project.slug,
            "generated_at": evaluation["generated_at"],
            "total_items": evaluation["items"]["total"],
            "by_status": evaluation["items"]["by_status"],
            "progress": evaluation["progress"],
            "progress_money": evaluation["progress_money"],
            "forecast": {
                "status": forecast.get("status"),
                "p50": forecast.get("p50"),
                "p90": forecast.get("p90"),
                "pct_completed": forecast.get("pct_completed"),
            },
            "health": evaluation["health"],
        }


async def evaluate_project(project_slug: str, n_iterations: int = 2000) -> dict[str, Any]:
    """Full project evaluation: RICE, EVM, Monte Carlo and health."""

    async with session_scope() as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found")
        return await evaluate_project_service(session, project, n_iterations=n_iterations)


async def forecast_project(project_slug: str) -> dict[str, Any]:
    """Forecast-only view for agents that do not need the full evaluation."""

    evaluation = await evaluate_project(project_slug, n_iterations=2000)
    return {
        "project": evaluation["project"],
        "forecast": evaluation["forecast"],
        "progress": evaluation["progress"],
    }


async def import_backlog(
    project_slug: str,
    path: str | None = None,
    dry_run: bool = False,
    skip_backup: bool = False,
) -> dict[str, Any]:
    """Import existing file backlogs into a project (backup first)."""

    async with session_scope() as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found")
        source = Path(path).expanduser() if path else None
        report = await import_backlog_service(
            session,
            project,
            source_path=source,
            dry_run=dry_run,
            skip_backup=skip_backup,
        )
        payload = report.to_dict()
        payload["project"] = project.slug
        payload["dry_run"] = dry_run
        return payload


async def backup_db(backup_dir: str | None = None, keep: int | None = None) -> dict[str, Any]:
    """Create an online SQLite backup and return its path (with retention)."""

    target = await create_backup(
        backup_dir=Path(backup_dir).expanduser() if backup_dir else None,
        keep=keep,
    )
    return {"status": "ok", "path": str(target)}


async def ingest_metrics(
    project_slug: str | None = None,
    tasks_root: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ingest Cline task history into sessions and metrics."""

    async with session_scope() as session:
        report = await ingest_cline_tasks(
            session,
            project_slug=project_slug,
            tasks_root=Path(tasks_root).expanduser() if tasks_root else None,
            limit=limit,
            dry_run=dry_run,
        )
        payload = report.to_dict()
        payload["dry_run"] = dry_run
        return payload


async def log_activity(
    project_slug: str,
    work_item_id: str | None = None,
    agent_name: str = "human",
    source: str = "manual",
    external_id: str | None = None,
    model: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    duration_ms: int = 0,
    active_ms: int = 0,
    cost_usd: float | None = None,
    success: bool = True,
) -> dict[str, Any]:
    """Record a manual session/metric (active collection)."""

    async with session_scope() as session:
        return await log_activity_service(
            session,
            project_slug=project_slug,
            work_item_id=work_item_id,
            agent_name=agent_name,
            source=source,
            external_id=external_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            duration_ms=duration_ms,
            active_ms=active_ms,
            cost_usd=cost_usd,
            success=success,
        )


async def task_metrics(item_id: str) -> dict[str, Any]:
    """Return aggregated time/token/cost metrics for a task."""

    async with session_scope() as session:
        return await task_metrics_service(session, item_id)


async def metrics_report(
    project_slug: str | None = None,
    group_by: str = "model",
    since: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate metrics by model, agent, task or project."""

    async with session_scope() as session:
        return await metrics_report_service(
            session,
            project_slug=project_slug,
            group_by=group_by,
            since=since,
        )


def _serialize_note(note: Note) -> dict[str, Any]:
    """Serialise a note for MCP responses."""

    return {
        "id": note.id,
        "project_id": note.project_id,
        "work_item_id": note.work_item_id,
        "kind": note.kind.value,
        "content": note.content,
        "author": note.author,
        "meta": note.meta or {},
        "created_at": note.created_at.isoformat(),
    }


async def add_note(
    project_slug: str,
    content: str,
    work_item_id: str | None = None,
    kind: str = "comment",
    author: str = "mcp-client",
) -> dict[str, Any]:
    """Append a note to a project, optionally attached to a work item."""

    try:
        note_kind = NoteKind(kind)
    except ValueError as exc:
        raise ValueError(f"unknown note kind: {kind}") from exc
    async with session_scope() as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found")
        if work_item_id is not None:
            item = await get_item(session, work_item_id)
            if item is None:
                raise ValueError(f"work item '{work_item_id}' not found")
        note = await repo_add_note(
            session,
            project_id=project.id,
            content=content,
            work_item_id=work_item_id,
            kind=note_kind,
            author=author,
        )
        await session.commit()
        return _serialize_note(note)


async def search_notes(
    project_slug: str,
    query: str,
    kind: str | None = None,
    work_item_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search note content by substring within a project."""

    note_kind: NoteKind | None = None
    if kind:
        try:
            note_kind = NoteKind(kind)
        except ValueError as exc:
            raise ValueError(f"unknown note kind: {kind}") from exc
    async with session_scope() as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found")
        notes = await repo_search_notes(
            session,
            project.id,
            query,
            work_item_id=work_item_id,
            kind=note_kind,
            limit=limit,
        )
        return [_serialize_note(note) for note in notes]


def _doc_type(value: str) -> DocType:
    try:
        return DocType(value)
    except ValueError as exc:
        raise ValueError(f"unknown document type: {value}") from exc


def _serialize_document(document: Document, *, include_content: bool) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": document.id,
        "project_id": document.project_id,
        "type": document.type.value,
        "slug": document.slug,
        "title": document.title,
        "version": document.version,
        "status": document.status,
        "created_at": document.created_at.isoformat(),
    }
    if include_content:
        data["content"] = document.content
    return data


async def upsert_document(
    project_slug: str,
    type: str,
    slug: str,
    title: str,
    content: str,
) -> dict[str, Any]:
    """Create or version-bump a project document (spec/adr/runbook/readme/design)."""

    doc_type = _doc_type(type)
    async with session_scope() as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found")
        result = await upsert_document_service(
            session,
            project_id=project.id,
            doc_type=doc_type,
            slug=slug,
            title=title,
            content=content,
        )
        await session.commit()
        payload = _serialize_document(result.document, include_content=False)
        payload["project"] = project.slug
        payload["created"] = result.created
        return payload


async def get_document(project_slug: str, type: str, slug: str) -> dict[str, Any]:
    """Return a single document with its full content."""

    doc_type = _doc_type(type)
    async with session_scope() as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found")
        document = await repo_get_document(session, project.id, doc_type, slug)
        if document is None:
            raise ValueError(f"document '{type}/{slug}' not found")
        return _serialize_document(document, include_content=True)


async def list_documents(project_slug: str, type: str | None = None) -> list[dict[str, Any]]:
    """List active documents for a project."""

    doc_type = _doc_type(type) if type else None
    async with session_scope() as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found")
        documents = await repo_list_documents(session, project.id, doc_type=doc_type)
        return [_serialize_document(document, include_content=False) for document in documents]


def _serialize_template(template: TaskTemplate) -> dict[str, Any]:
    return {
        "id": template.id,
        "name": template.name,
        "type": template.type,
        "project_id": template.project_id,
    }


async def list_templates(project_slug: str | None = None) -> list[dict[str, Any]]:
    """List global and project-scoped templates."""

    async with session_scope() as session:
        project_id: str | None = None
        if project_slug:
            project = await get_project_by_slug(session, project_slug)
            if project is None:
                raise ValueError(f"project '{project_slug}' not found")
            project_id = project.id
        templates = await repo_list_templates(session, project_id=project_id)
        return [_serialize_template(template) for template in templates]


async def apply_template(
    project_slug: str,
    template_name: str,
    title: str | None = None,
    variables: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a work item from a stored markdown template."""

    async with session_scope() as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found")
        template = await repo_get_template(session, template_name, project_id=project.id)
        if template is None:
            raise ValueError(f"template '{template_name}' not found")
        item = await apply_template_service(
            session,
            project=project,
            template=template,
            title=title,
            variables=variables,
        )
        await session.commit()
        return serialize_item(item, compact=False)


async def import_templates(
    paths: list[str] | None = None,
    project_slug: str | None = None,
) -> dict[str, Any]:
    """Import markdown templates from disk into the template store."""

    async with session_scope() as session:
        project_id: str | None = None
        if project_slug:
            project = await get_project_by_slug(session, project_slug)
            if project is None:
                raise ValueError(f"project '{project_slug}' not found")
            project_id = project.id
        roots = (
            [Path(path).expanduser() for path in paths] if paths else list(DEFAULT_TEMPLATE_SOURCES)
        )
        report = await import_templates_service(session, source_roots=roots, project_id=project_id)
        await session.commit()
        return report.to_dict()


def _serialize_agent(agent: Agent) -> dict[str, Any]:
    return {
        "id": agent.id,
        "name": agent.name,
        "kind": agent.kind,
        "role": agent.role.value,
        "meta": agent.meta or {},
    }


def _serialize_claim(
    claim: Claim,
    item: WorkItem | None = None,
    agent: Agent | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": claim.id,
        "work_item_id": claim.work_item_id,
        "agent_id": claim.agent_id,
        "acquired_at": claim.acquired_at.isoformat(),
        "expires_at": claim.expires_at.isoformat(),
        "released_at": claim.released_at.isoformat() if claim.released_at else None,
    }
    if item is not None:
        data["item"] = {
            "id": item.id,
            "title": item.title,
            "status": item.status.value,
            "project_id": item.project_id,
        }
    if agent is not None:
        data["agent"] = _serialize_agent(agent)
    return data


async def register_agent(
    name: str,
    kind: str = "mcp-client",
    role: str = "agent",
) -> dict[str, Any]:
    """Register (or return) an agent that can hold claim leases."""

    try:
        role_enum = AgentRole(role)
    except ValueError as exc:
        raise ValueError(f"unknown role: {role}") from exc
    async with session_scope() as session:
        agent = await repo_get_or_create_agent(session, name=name, kind=kind, role=role_enum)
        await session.commit()
        return _serialize_agent(agent)


async def claim_task(
    item_id: str, agent_name: str, ttl_minutes: int | None = None
) -> dict[str, Any]:
    """Acquire a TTL lease on a work item (renews if already held)."""

    async with session_scope() as session:
        try:
            result = await acquire_claim_service(
                session,
                item_id=item_id,
                agent_name=agent_name,
                ttl_minutes=ttl_minutes,
            )
            await session.commit()
        except ClaimError as exc:
            raise ValueError(str(exc)) from exc
        return {
            **_serialize_claim(result.claim, result.item, result.agent),
            "renewed": result.renewed,
        }


async def renew_claim(
    item_id: str, agent_name: str, ttl_minutes: int | None = None
) -> dict[str, Any]:
    """Renew an existing claim lease held by the agent."""

    return await claim_task(item_id, agent_name, ttl_minutes)


async def release_task(item_id: str, agent_name: str) -> dict[str, Any]:
    """Release the agent's active leases on a work item."""

    async with session_scope() as session:
        released = await release_claim_service(session, item_id=item_id, agent_name=agent_name)
        await session.commit()
        return {"item_id": item_id, "agent": agent_name, "released": released}


async def list_claims(project_slug: str | None = None) -> list[dict[str, Any]]:
    """List active (unexpired, unreleased) claim leases."""

    async with session_scope() as session:
        project_id: str | None = None
        if project_slug:
            project = await get_project_by_slug(session, project_slug)
            if project is None:
                raise ValueError(f"project '{project_slug}' not found")
            project_id = project.id
        claims = await repo_list_active_claims(session, project_id=project_id)
        result: list[dict[str, Any]] = []
        for claim in claims:
            item = await get_item(session, claim.work_item_id)
            agent = await session.get(Agent, claim.agent_id)
            result.append(_serialize_claim(claim, item, agent))
        return result


async def list_tasks(
    project_slug: str,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List work items for a project, optionally filtered by status."""

    status_enum: WorkStatus | None = None
    if status:
        try:
            status_enum = WorkStatus(status)
        except ValueError as exc:
            raise ValueError(f"unknown status: {status}") from exc
    async with session_scope() as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found")
        items = await repo_list_items(session, project.id, status=status_enum, limit=limit)
        return [serialize_item(item) for item in items]


async def register_projects(
    root: str | None = None,
    max_depth: int = 2,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Discover project directories with task sources and register them."""

    async with session_scope() as session:
        base = Path(root).expanduser() if root else get_settings().repositories_root
        try:
            report = await register_projects_service(
                session, base, max_depth=max_depth, dry_run=dry_run
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        payload = report.to_dict()
        payload["root"] = str(base)
        payload["dry_run"] = dry_run
        return payload


async def sync_projects(
    root: str | None = None,
    max_depth: int = 3,
    dry_run: bool = False,
    archive_missing: bool = False,
    archive_duplicates: bool = False,
    moves: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Align the project registry with the folders on disk.

    Identity is the resolved root path, so a known path never creates a second
    slug. ``moves`` re-points a slug at a new checkout and keeps its work items;
    archiving is soft and refuses to hide data.
    """

    async with session_scope() as session:
        base = Path(root).expanduser() if root else get_settings().repositories_root
        try:
            report = await sync_projects_service(
                session,
                base,
                max_depth=max_depth,
                dry_run=dry_run,
                moves=moves,
                archive_missing=archive_missing,
                archive_duplicates=archive_duplicates,
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        payload = report.to_dict()
        payload["root"] = str(base)
        payload["dry_run"] = dry_run
        return payload


async def project_audit(
    root: str | None = None,
    max_depth: int = 3,
) -> dict[str, Any]:
    """Report stale roots, duplicate registrations and uncovered directories."""

    async with session_scope() as session:
        base = Path(root).expanduser() if root else get_settings().repositories_root
        try:
            report = await audit_projects_service(session, base, max_depth=max_depth)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return report.to_dict()


async def move_project(
    slug: str,
    root: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Re-point a project at a new checkout; the slug and the work items stay."""

    async with session_scope() as session:
        return await move_project_service(session, slug, root, dry_run=dry_run)


async def archive_project(
    slug: str,
    force: bool = False,
    restore: bool = False,
) -> dict[str, Any]:
    """Soft-archive (or restore) a project; data-bearing projects need force."""

    async with session_scope() as session:
        return await archive_project_service(session, slug, force=force, restore=restore)


async def import_all_backlogs(
    dry_run: bool = False,
    skip_backup: bool = False,
    project_slugs: list[str] | None = None,
    include_notes: bool = False,
) -> dict[str, Any]:
    """Import backlogs for every registered project (or a subset)."""

    async with session_scope() as session:
        projects = await repo_list_projects(session)
        if project_slugs:
            wanted = set(project_slugs)
            known = {project.slug for project in projects}
            missing = wanted - known
            if missing:
                raise ValueError(f"projects not found: {', '.join(sorted(missing))}")
            projects = [project for project in projects if project.slug in wanted]
        report = await import_all_service(
            session,
            projects,
            dry_run=dry_run,
            skip_backup=skip_backup,
            include_notes=include_notes,
        )
        payload = report.to_dict()
        payload["dry_run"] = dry_run
        payload["project_count"] = len(projects)
        return payload


async def import_notes(
    project_slugs: list[str] | None = None,
    dry_run: bool = False,
    skip_backup: bool = False,
    include_memory: bool = True,
) -> dict[str, Any]:
    """Import ledgers/notes for registered projects and the global memory."""

    async with session_scope() as session:
        projects = await repo_list_projects(session)
        if project_slugs:
            wanted = set(project_slugs)
            known = {project.slug for project in projects}
            missing = wanted - known
            if missing:
                raise ValueError(f"projects not found: {', '.join(sorted(missing))}")
            projects = [project for project in projects if project.slug in wanted]
        payload = await import_all_notes_service(
            session,
            projects,
            include_memory=include_memory,
            dry_run=dry_run,
            skip_backup=skip_backup,
        )
        payload["project_count"] = len(projects)
        return payload


async def export_notes(
    project_slug: str | None = None,
    kind: str | None = None,
    ledger_type: str | None = None,
    since: str | None = None,
    output_format: str = "markdown",
    limit: int = 500,
) -> dict[str, Any]:
    """Export a notes digest (markdown or JSON) for writing posts."""

    async with session_scope() as session:
        text = await export_notes_service(
            session,
            project_slug=project_slug,
            kind=kind,
            ledger_type=ledger_type,
            since=since,
            output_format=output_format,
            limit=limit,
        )
        return {
            "format": output_format,
            "filters": {
                "project": project_slug,
                "kind": kind,
                "ledger_type": ledger_type,
                "since": since,
            },
            "content": text,
        }


# ---------------------------------------------------------------------------
# Agent economics (M11)
# ---------------------------------------------------------------------------


async def economics_report(
    project_slug: str | None = None,
    group_by: str = "model",
    since: str | None = None,
) -> list[dict[str, Any]]:
    """Money view of the agent usage, grouped by model/agent/task/project."""

    async with session_scope() as session:
        return await economics_report_service(
            session,
            project_slug=project_slug,
            group_by=group_by,
            since=_parse_dt(since),
        )


async def economics_summary(project_slug: str) -> dict[str, Any]:
    """Totals, human baseline, savings, ROI and the money-scale EVM."""

    async with session_scope() as session:
        return await economics_summary_service(session, project_slug=project_slug)


async def cost_series(
    project_slug: str | None = None,
    bucket: str = "day",
    since: str | None = None,
) -> dict[str, Any]:
    """Burn-rate series of the agent spend (day/week/month buckets)."""

    async with session_scope() as session:
        return await cost_series_service(
            session,
            project_slug=project_slug,
            bucket=bucket,
            since=_parse_dt(since),
        )


async def list_prices(model: str | None = None) -> list[dict[str, Any]]:
    """Known model prices (database first, then the bundled price list)."""

    async with session_scope() as session:
        db_patterns = {
            row.pattern for row in (await session.execute(select(ModelPrice))).scalars().all()
        }
        specs = await load_price_specs(session)
    if model:
        needle = model.strip().lower()
        specs = [spec for spec in specs if needle in spec.pattern.lower()]
    return [
        {**spec.to_dict(), "source": "db" if spec.pattern in db_patterns else "yaml"}
        for spec in specs
    ]


async def sync_prices(dry_run: bool = True) -> dict[str, Any]:
    """Apply the declarative price list to the database (dry run by default)."""

    from taskhub.prices import load_prices

    async with session_scope() as session:
        specs = load_prices().to_specs()
        report = await sync_prices_service(session, specs, dry_run=dry_run)
        if not dry_run:
            await session.commit()
    payload = report.to_dict()
    payload["dry_run"] = dry_run
    return payload


async def set_model_price(
    pattern: str,
    input_usd_per_mtok: float = 0.0,
    output_usd_per_mtok: float = 0.0,
    cache_read_usd_per_mtok: float = 0.0,
    cache_write_usd_per_mtok: float = 0.0,
    provider: str | None = None,
    currency: str = "USD",
    valid_from: str | None = None,
    valid_to: str | None = None,
) -> dict[str, Any]:
    """Create or update the price of a model pattern (never deletes)."""

    start = _parse_dt(valid_from)
    end = _parse_dt(valid_to)
    async with session_scope() as session:
        result = await set_price_service(
            session,
            pattern=pattern,
            input_usd_per_mtok=input_usd_per_mtok,
            output_usd_per_mtok=output_usd_per_mtok,
            cache_read_usd_per_mtok=cache_read_usd_per_mtok,
            cache_write_usd_per_mtok=cache_write_usd_per_mtok,
            provider=provider,
            currency=currency,
            valid_from=start,
            valid_to=end,
        )
        await session.commit()
        return {
            "pattern": result.price.pattern,
            "created": result.created,
            "source": "db",
            **result.price.to_spec().to_dict(),
        }


async def close_model_price(pattern: str, valid_from: str) -> dict[str, Any]:
    """Close a price by setting its end date; the row is kept."""

    start = _parse_dt(valid_from)
    if start is None:
        raise ValueError("valid_from is required to close a price")
    async with session_scope() as session:
        row = await close_price_service(session, pattern=pattern, valid_from=start)
        await session.commit()
        return {
            "pattern": row.pattern,
            "closed": True,
            "source": "db",
            **row.to_spec().to_dict(),
        }


async def set_project_economics(
    project_slug: str,
    human_rate_usd_per_hour: float | None = None,
    error_cost_usd: float | None = None,
    supervision_cost_usd: float | None = None,
) -> dict[str, Any]:
    """Set the money rates of a project (human rate, error price, supervision)."""

    async with session_scope() as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found")
        rates = await set_project_rates_service(
            session,
            project,
            human_rate_usd_per_hour=human_rate_usd_per_hour,
            error_cost_usd=error_cost_usd,
            supervision_cost_usd=supervision_cost_usd,
        )
        await add_audit_note(
            session,
            project_id=project.id,
            content=f"economics rates updated: {rates}",
            meta={"tool": "set_project_economics", "rates": rates},
        )
        await session.commit()
        return {
            "project": project.slug,
            "human_rate_usd_per_hour": rates.get("human_rate_usd_per_hour"),
            "error_cost_usd": rates.get("error_cost_usd"),
            "supervision_cost_usd": rates.get("supervision_cost_usd"),
        }


async def portfolio_summary() -> dict[str, Any]:
    """Summarise the whole portfolio: projects, items and statuses."""

    async with session_scope() as session:
        return await portfolio_summary_service(session)
