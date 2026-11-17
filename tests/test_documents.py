"""Tests for versioned project documents (M6)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import DocType, Document
from taskhub.repo.documents import get_document, list_documents
from taskhub.repo.projects import create_project
from taskhub.services.documents import upsert_document


async def _project(session: AsyncSession):
    project = await create_project(session, slug="p", name="P", root_path="/tmp/p")
    await session.commit()
    return project


async def test_upsert_document_creates_then_versions(
    session: AsyncSession,
) -> None:
    project = await _project(session)

    first = await upsert_document(
        session,
        project_id=project.id,
        doc_type=DocType.spec,
        slug="architecture",
        title="Architecture",
        content="v1",
    )
    await session.commit()
    assert first.created is True
    assert first.document.version == 1

    second = await upsert_document(
        session,
        project_id=project.id,
        doc_type=DocType.spec,
        slug="architecture",
        title="Architecture v2",
        content="v2",
    )
    await session.commit()
    assert second.created is False
    assert second.document.version == 2
    assert second.document.content == "v2"

    rows = list((await session.execute(select(Document))).scalars().all())
    assert len(rows) == 1


async def test_list_documents_filters_by_type(session: AsyncSession) -> None:
    project = await _project(session)
    await upsert_document(
        session,
        project_id=project.id,
        doc_type=DocType.spec,
        slug="spec-one",
        title="Spec",
        content="x",
    )
    await upsert_document(
        session,
        project_id=project.id,
        doc_type=DocType.adr,
        slug="adr-one",
        title="ADR",
        content="y",
    )
    await session.commit()

    specs = await list_documents(session, project.id, doc_type=DocType.spec)
    assert [document.slug for document in specs] == ["spec-one"]

    all_docs = await list_documents(session, project.id)
    assert {document.slug for document in all_docs} == {"spec-one", "adr-one"}


async def test_get_document_returns_latest(session: AsyncSession) -> None:
    project = await _project(session)
    await upsert_document(
        session,
        project_id=project.id,
        doc_type=DocType.runbook,
        slug="deploy",
        title="Deploy",
        content="steps",
    )
    await session.commit()

    document = await get_document(session, project.id, DocType.runbook, "deploy")
    assert document is not None
    assert document.title == "Deploy"
    assert await get_document(session, project.id, DocType.runbook, "missing") is None
