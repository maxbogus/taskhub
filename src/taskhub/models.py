"""SQLAlchemy 2.0 models for TaskHub."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JsonType = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Declarative base for all TaskHub tables."""


class WorkItemType(enum.StrEnum):
    epic = "epic"
    feature = "feature"
    task = "task"
    subtask = "subtask"


class WorkStatus(enum.StrEnum):
    backlog = "backlog"
    ready = "ready"
    in_progress = "in_progress"
    blocked = "blocked"
    review = "review"
    done = "done"
    cancelled = "cancelled"
    archived = "archived"


class DependencyType(enum.StrEnum):
    blocks = "blocks"
    blocked_by = "blocked_by"
    relates = "relates"
    duplicates = "duplicates"


class NoteKind(enum.StrEnum):
    attempt = "attempt"
    blocker = "blocker"
    insight = "insight"
    comment = "comment"
    decision = "decision"
    handoff = "handoff"
    ledger = "ledger"


class DocType(enum.StrEnum):
    spec = "spec"
    adr = "adr"
    runbook = "runbook"
    readme = "readme"
    design = "design"


class SessionSource(enum.StrEnum):
    cline = "cline"
    claude_code = "claude_code"
    cursor = "cursor"
    manual = "manual"
    imported = "imported"


class AgentRole(enum.StrEnum):
    agent = "agent"
    human = "human"
    reviewer = "reviewer"


def _enum(enum_cls: type[enum.Enum]) -> Enum:
    """Portable enum column: stored as text, works on SQLite and PostgreSQL."""

    return Enum(
        enum_cls,
        native_enum=False,
        values_callable=lambda e: [member.value for member in e],
    )


class Project(Base):
    __tablename__ = "project"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    root_path: Mapped[str] = mapped_column(String(1024))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    instances: Mapped[list[Instance]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    work_items: Mapped[list[WorkItem]] = relationship(back_populates="project")
    documents: Mapped[list[Document]] = relationship(back_populates="project")
    templates: Mapped[list[TaskTemplate]] = relationship(back_populates="project")
    sessions: Mapped[list[Session]] = relationship(back_populates="project")


class Instance(Base):
    __tablename__ = "instance"
    __table_args__ = (
        UniqueConstraint("project_id", "hostname", "container_id", name="uq_instance"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    hostname: Mapped[str] = mapped_column(String(255))
    container_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    environment: Mapped[str] = mapped_column(String(32), default="local")
    config: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped[Project] = relationship(back_populates="instances")


class WorkItem(Base):
    __tablename__ = "work_item"
    __table_args__ = (
        UniqueConstraint("project_id", "external_ref", name="uq_work_item_external_ref"),
        Index("ix_work_item_status_priority_order", "status", "priority", "order_index"),
        Index("ix_work_item_project_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    instance_id: Mapped[str | None] = mapped_column(
        ForeignKey("instance.id"), nullable=True
    )
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_item.id"), nullable=True, index=True
    )
    type: Mapped[WorkItemType] = mapped_column(
        _enum(WorkItemType), default=WorkItemType.task
    )
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[WorkStatus] = mapped_column(
        _enum(WorkStatus), default=WorkStatus.backlog, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, default=3, index=True)
    order_index: Mapped[float] = mapped_column(Float, default=1000.0)
    estimate_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    required_skill: Mapped[str | None] = mapped_column(String(120), nullable=True)
    acceptance_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped[Project] = relationship(back_populates="work_items")
    parent: Mapped[WorkItem | None] = relationship(
        remote_side="WorkItem.id", back_populates="children"
    )
    children: Mapped[list[WorkItem]] = relationship(
        back_populates="parent", cascade="save-update, merge"
    )
    notes: Mapped[list[Note]] = relationship(
        back_populates="work_item", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[Session]] = relationship(back_populates="work_item")
    outgoing: Mapped[list[Dependency]] = relationship(
        foreign_keys="Dependency.from_id",
        back_populates="from_item",
        cascade="all, delete-orphan",
    )
    incoming: Mapped[list[Dependency]] = relationship(
        foreign_keys="Dependency.to_id",
        back_populates="to_item",
        cascade="all, delete-orphan",
    )


class Dependency(Base):
    """Directed dependency.

    ``from_id`` is the blocker, ``to_id`` is the blocked item. For
    ``blocked_by`` the meaning reads "to_id is blocked by from_id"; for
    ``blocks`` it reads "from_id blocks to_id". Both are normalised the same
    way so a single query can resolve blockers.
    """

    __tablename__ = "dependency"
    __table_args__ = (
        UniqueConstraint("from_id", "to_id", "type", name="uq_dependency"),
        Index("ix_dependency_to_type", "to_id", "type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    from_id: Mapped[str] = mapped_column(
        ForeignKey("work_item.id", ondelete="CASCADE"), index=True
    )
    to_id: Mapped[str] = mapped_column(
        ForeignKey("work_item.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[DependencyType] = mapped_column(
        _enum(DependencyType), default=DependencyType.blocked_by
    )

    from_item: Mapped[WorkItem] = relationship(
        foreign_keys=[from_id], back_populates="outgoing"
    )
    to_item: Mapped[WorkItem] = relationship(
        foreign_keys=[to_id], back_populates="incoming"
    )


class Note(Base):
    __tablename__ = "note"
    __table_args__ = (
        Index("ix_note_project_created", "project_id", "created_at"),
        Index("ix_note_item_created", "work_item_id", "created_at"),
        Index("uq_note_external_ref", "project_id", "external_ref", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    work_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_item.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[NoteKind] = mapped_column(
        _enum(NoteKind), default=NoteKind.comment
    )
    content: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Stable key of the imported source entry, e.g.
    # "docs/ledger/decisions.jsonl:ADR-001". NULL for interactive notes;
    # SQLite/PostgreSQL treat NULLs as distinct in the unique index.
    external_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    work_item: Mapped[WorkItem | None] = relationship(back_populates="notes")


class Document(Base):
    __tablename__ = "document"
    __table_args__ = (
        UniqueConstraint("project_id", "type", "slug", name="uq_document"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[DocType] = mapped_column(_enum(DocType), default=DocType.spec)
    slug: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(512))
    content: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped[Project] = relationship(back_populates="documents")


class TaskTemplate(Base):
    __tablename__ = "task_template"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=True, index=True
    )
    type: Mapped[str] = mapped_column(String(32), default="task")
    name: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    project: Mapped[Project | None] = relationship(back_populates="templates")


class Agent(Base):
    __tablename__ = "agent"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), default="mcp-client")
    role: Mapped[AgentRole] = mapped_column(_enum(AgentRole), default=AgentRole.agent)
    meta: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Claim(Base):
    __tablename__ = "claim"
    __table_args__ = (Index("ix_claim_active", "work_item_id", "expires_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    work_item_id: Mapped[str] = mapped_column(
        ForeignKey("work_item.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[str] = mapped_column(ForeignKey("agent.id"), index=True)
    acquired_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Session(Base):
    __tablename__ = "session"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("project.id", ondelete="SET NULL"), nullable=True, index=True
    )
    work_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_item.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent.id"), nullable=True
    )
    source: Mapped[SessionSource] = mapped_column(
        _enum(SessionSource), default=SessionSource.manual
    )
    external_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    model: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)

    work_item: Mapped[WorkItem | None] = relationship(back_populates="sessions")
    project: Mapped[Project | None] = relationship(back_populates="sessions")
    metrics: Mapped[list[Metric]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class Metric(Base):
    __tablename__ = "metric"
    __table_args__ = (Index("ix_metric_recorded", "recorded_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.id", ondelete="CASCADE"), index=True
    )
    model: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    active_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    session: Mapped[Session] = relationship(back_populates="metrics")
