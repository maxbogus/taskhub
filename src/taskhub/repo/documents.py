"""Document repository: versioned project documents."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import DocType, Document


async def get_document(
    session: AsyncSession, project_id: str, doc_type: DocType, slug: str
) -> Document | None:
    """Return a single document for a project/type/slug tuple."""

    stmt = select(Document).where(
        Document.project_id == project_id,
        Document.type == doc_type,
        Document.slug == slug,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def create_document(
    session: AsyncSession,
    *,
    project_id: str,
    doc_type: DocType,
    slug: str,
    title: str,
    content: str,
    status: str = "active",
) -> Document:
    """Insert a new document at version 1."""

    document = Document(
        project_id=project_id,
        type=doc_type,
        slug=slug,
        title=title,
        content=content,
        version=1,
        status=status,
    )
    session.add(document)
    await session.flush()
    return document


async def list_documents(
    session: AsyncSession,
    project_id: str,
    *,
    doc_type: DocType | None = None,
    include_archived: bool = False,
    limit: int = 100,
) -> list[Document]:
    """List active documents for a project, optionally filtered by type."""

    stmt = select(Document).where(Document.project_id == project_id)
    if doc_type is not None:
        stmt = stmt.where(Document.type == doc_type)
    if not include_archived:
        stmt = stmt.where(Document.archived_at.is_(None))
    stmt = stmt.order_by(Document.type.asc(), Document.slug.asc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())
