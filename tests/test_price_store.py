"""Tests for writing model prices and project rates (M11).

The write path owns three rules: prices are keyed by
``(pattern, valid_from)``, a price is closed instead of deleted, and the
human rate is always returned so the number is never anonymous.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import ModelPrice, Note, NoteKind
from taskhub.repo.projects import create_project
from taskhub.services.price_store import (
    add_audit_note,
    close_price,
    set_price,
    set_project_rates,
    sync_prices,
)

PRICES = "src/taskhub/prices.yaml"


async def test_set_price_creates_then_updates(session: AsyncSession) -> None:
    """The same key updates the row instead of duplicating it."""
    valid_from = datetime(2026, 1, 1)

    created = await set_price(
        session, pattern="test-*", input_usd_per_mtok=3.0, valid_from=valid_from
    )
    updated = await set_price(
        session, pattern="test-*", input_usd_per_mtok=4.0, valid_from=valid_from
    )
    await session.commit()

    assert created.created is True
    assert updated.created is False
    assert updated.price.input_usd_micro == 4_000_000

    rows = list((await session.execute(select(ModelPrice))).scalars().all())
    assert len(rows) == 1


async def test_set_price_rejects_blank_pattern(session: AsyncSession) -> None:
    """An empty glob would match every model."""
    with pytest.raises(ValueError, match="pattern"):
        await set_price(session, pattern="  ")


async def test_set_price_rejects_negative_rate(session: AsyncSession) -> None:
    """A negative rate would invert the cost."""
    with pytest.raises(ValueError):
        await set_price(session, pattern="test-*", input_usd_per_mtok=-1.0)


async def test_close_price_sets_valid_to(session: AsyncSession) -> None:
    """Prices are archived by closing them, never deleted."""
    valid_from = datetime(2026, 1, 1)
    await set_price(session, pattern="test-*", input_usd_per_mtok=3.0, valid_from=valid_from)

    closed = await close_price(
        session,
        pattern="test-*",
        valid_from=valid_from,
        closed_at=datetime(2026, 6, 1),
    )
    await session.commit()

    assert closed.valid_to == datetime(2026, 6, 1)
    rows = list((await session.execute(select(ModelPrice))).scalars().all())
    assert len(rows) == 1


async def test_close_price_requires_existing_row(session: AsyncSession) -> None:
    """Closing an unknown price is a clear error."""
    with pytest.raises(ValueError, match="not found"):
        await close_price(session, pattern="absent-*", valid_from=datetime(2026, 1, 1))


async def test_sync_prices_is_idempotent(session: AsyncSession) -> None:
    """Running the sync twice creates the price only once."""
    from taskhub.prices import load_prices

    specs = load_prices(PRICES).to_specs()

    first = await sync_prices(session, specs, dry_run=False)
    second = await sync_prices(session, specs, dry_run=False)
    await session.commit()

    assert first.created == len(specs)
    assert second.created == 0
    assert second.unchanged == len(specs)

    rows = list((await session.execute(select(ModelPrice))).scalars().all())
    assert len(rows) == len(specs)


async def test_sync_prices_dry_run_writes_nothing(session: AsyncSession) -> None:
    """A dry run reports the changes without touching the database."""
    from taskhub.prices import load_prices

    specs = load_prices(PRICES).to_specs()

    report = await sync_prices(session, specs, dry_run=True)
    await session.commit()

    assert report.created == len(specs)
    assert report.applied == len(specs)
    rows = list((await session.execute(select(ModelPrice))).scalars().all())
    assert rows == []


async def test_set_project_rates_stores_override(session: AsyncSession) -> None:
    """The human rate lands in the project config."""
    project = await create_project(session, slug="p", name="P", root_path="/tmp/p")

    rates = await set_project_rates(
        session, project, human_rate_usd_per_hour=75.0, error_cost_usd=5.0
    )
    await session.commit()

    assert rates["human_rate_usd_per_hour"] == 75.0
    assert rates["error_cost_usd"] == 5.0
    assert project.config["economics"]["human_rate_usd_per_hour"] == 75.0


async def test_set_project_rates_keeps_other_values(session: AsyncSession) -> None:
    """Only the given fields are changed."""
    project = await create_project(
        session,
        slug="p",
        name="P",
        root_path="/tmp/p",
        config={"economics": {"human_rate_usd_per_hour": 60.0, "error_cost_usd": 9.0}},
    )

    rates = await set_project_rates(session, project, error_cost_usd=1.0)
    await session.commit()

    assert rates["human_rate_usd_per_hour"] == 60.0
    assert rates["error_cost_usd"] == 1.0


async def test_set_project_rates_rejects_invalid_values(session: AsyncSession) -> None:
    """Invalid rates are rejected at the boundary."""
    project = await create_project(session, slug="p", name="P", root_path="/tmp/p")

    with pytest.raises(ValueError):
        await set_project_rates(session, project, human_rate_usd_per_hour=0.0)
    with pytest.raises(ValueError):
        await set_project_rates(session, project, error_cost_usd=-1.0)


async def test_add_audit_note_writes_decision(session: AsyncSession) -> None:
    """Every economics change leaves a decision note behind."""
    project = await create_project(session, slug="p", name="P", root_path="/tmp/p")

    note = await add_audit_note(
        session,
        project_id=project.id,
        content="price updated",
        meta={"tool": "set_model_price"},
    )
    await session.commit()

    assert note.kind is NoteKind.decision
    rows = list((await session.execute(select(Note))).scalars().all())
    assert len(rows) == 1
