"""Document upsert with monotonic versioning (M6)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import DocType, Document
from taskhub.repo.documents import create_document, get_document


@dataclass(frozen=True)
class DocumentUpsert:
    """Result of an upsert: the stored document plus a creation flag."""

    document: Document
    created: bool


async def upsert_document(
    session: AsyncSession,
    *,
    project_id: str,
    doc_type: DocType,
    slug: str,
    title: str,
    content: str,
) -> DocumentUpsert:
    """Create a document or bump its version when it already exists."""

    existing = await get_document(session, project_id, doc_type, slug)
    if existing is None:
        created = await create_document(
            session,
            project_id=project_id,
            doc_type=doc_type,
            slug=slug,
            title=title,
            content=content,
        )
        return DocumentUpsert(document=created, created=True)

    existing.title = title
    existing.content = content
    existing.version += 1
    await session.flush()
    return DocumentUpsert(document=existing, created=False)
