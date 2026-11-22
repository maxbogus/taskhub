"""Writing model prices and project rates (M11).

The read side lives in ``services/economics.py``; this module owns every
write path so the rules stay in one place:

* prices are keyed by ``(pattern, valid_from)`` — re-running the same
  import updates the row instead of duplicating it;
* a price is **closed**, never deleted: closing it sets ``valid_to``;
* every write leaves an audit note in the project, so the reason for a
  price change is visible in ``search_notes`` and in the export digest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import ModelPrice, Note, NoteKind, Project
from taskhub.prices import MICRO_USD_PER_USD, PriceSpec, to_micro_usd


def _utcnow() -> datetime:
    """Current time as a naive UTC timestamp (database convention)."""
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class PriceSyncReport:
    """Result of a price-list sync."""

    applied: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return the report as a plain dictionary."""
        return {
            "applied": self.applied,
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class PriceWriteResult:
    """Result of writing one price."""

    price: ModelPrice
    created: bool


async def set_price(
    session: AsyncSession,
    *,
    pattern: str,
    input_usd_per_mtok: float = 0.0,
    output_usd_per_mtok: float = 0.0,
    cache_read_usd_per_mtok: float = 0.0,
    cache_write_usd_per_mtok: float = 0.0,
    provider: str | None = None,
    currency: str = "USD",
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
) -> PriceWriteResult:
    """Create or update the price of one model pattern.

    The row is identified by ``(pattern, valid_from)``. Writing the same
    key again updates the rates in place.

    Args:
        session: Database session.
        pattern: Glob matched against the recorded model name.
        input_usd_per_mtok: Price per 1M input tokens.
        output_usd_per_mtok: Price per 1M output tokens.
        cache_read_usd_per_mtok: Price per 1M cache-read tokens.
        cache_write_usd_per_mtok: Price per 1M cache-write tokens.
        provider: Vendor name.
        currency: Currency of the prices.
        valid_from: Start of the validity window; defaults to now.
        valid_to: End of the validity window; ``None`` means "still valid".

    Returns:
        The stored row and whether it was created.

    Raises:
        ValueError: The pattern is blank or a price is negative.
    """
    if not pattern.strip():
        raise ValueError("price pattern must not be empty")

    start = valid_from or _utcnow()
    rates = {
        "input_usd_micro": to_micro_usd(input_usd_per_mtok),
        "output_usd_micro": to_micro_usd(output_usd_per_mtok),
        "cache_read_usd_micro": to_micro_usd(cache_read_usd_per_mtok),
        "cache_write_usd_micro": to_micro_usd(cache_write_usd_per_mtok),
    }

    existing = (
        await session.execute(
            select(ModelPrice).where(
                ModelPrice.pattern == pattern.strip(),
                ModelPrice.valid_from == start,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        row = ModelPrice(
            pattern=pattern.strip(),
            provider=provider,
            currency=currency,
            valid_from=start,
            valid_to=valid_to,
            **rates,
        )
        session.add(row)
        await session.flush()
        return PriceWriteResult(price=row, created=True)

    for field_name, value in rates.items():
        setattr(existing, field_name, value)
    existing.provider = provider
    existing.currency = currency
    existing.valid_to = valid_to
    await session.flush()
    return PriceWriteResult(price=existing, created=False)


async def close_price(
    session: AsyncSession,
    *,
    pattern: str,
    valid_from: datetime,
    closed_at: datetime | None = None,
) -> ModelPrice:
    """Close a price by setting its ``valid_to``.

    Prices are never deleted: the project archives instead of erasing, and
    a closed price keeps the history of what the work cost at the time.

    Args:
        session: Database session.
        pattern: The price pattern to close.
        valid_from: Start of the validity window of the row to close.
        closed_at: When the price stops being valid; defaults to now.

    Returns:
        The closed row.

    Raises:
        ValueError: No such price exists.
    """
    row = (
        await session.execute(
            select(ModelPrice).where(
                ModelPrice.pattern == pattern.strip(),
                ModelPrice.valid_from == valid_from,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise ValueError(f"price '{pattern}' valid from {valid_from.isoformat()} not found")
    row.valid_to = closed_at or _utcnow()
    await session.flush()
    return row


async def sync_prices(
    session: AsyncSession,
    specs: list[PriceSpec],
    *,
    dry_run: bool = True,
) -> PriceSyncReport:
    """Apply a price list to the database, idempotently.

    Args:
        session: Database session.
        specs: The price specs to apply.
        dry_run: When true, nothing is written (the default).

    Returns:
        A report of what would change and what changed.
    """
    report = PriceSyncReport()
    for spec in specs:
        existing = (
            await session.execute(
                select(ModelPrice).where(
                    ModelPrice.pattern == spec.pattern,
                    ModelPrice.valid_from == spec.valid_from,
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            report.created += 1
            report.applied += 1
            if dry_run:
                continue
            await _apply_spec(session, spec)
            continue

        rates = (
            existing.input_usd_micro,
            existing.output_usd_micro,
            existing.cache_read_usd_micro,
            existing.cache_write_usd_micro,
        )
        wanted = (
            spec.input_usd_micro,
            spec.output_usd_micro,
            spec.cache_read_usd_micro,
            spec.cache_write_usd_micro,
        )
        if rates == wanted:
            report.unchanged += 1
            continue

        report.updated += 1
        report.applied += 1
        if dry_run:
            continue
        await _apply_spec(session, spec)

    return report


async def _apply_spec(session: AsyncSession, spec: PriceSpec) -> None:
    """Write one price spec into the database."""
    await set_price(
        session,
        pattern=spec.pattern,
        input_usd_per_mtok=spec.input_usd_micro / MICRO_USD_PER_USD,
        output_usd_per_mtok=spec.output_usd_micro / MICRO_USD_PER_USD,
        cache_read_usd_per_mtok=spec.cache_read_usd_micro / MICRO_USD_PER_USD,
        cache_write_usd_per_mtok=spec.cache_write_usd_micro / MICRO_USD_PER_USD,
        provider=spec.provider,
        currency=spec.currency,
        valid_from=spec.valid_from,
        valid_to=spec.valid_to,
    )


async def set_project_rates(
    session: AsyncSession,
    project: Project,
    *,
    human_rate_usd_per_hour: float | None = None,
    error_cost_usd: float | None = None,
    supervision_cost_usd: float | None = None,
) -> dict[str, Any]:
    """Update the economics rates of a project.

    Only the given values are changed; the others keep their current
    value. The result is stored in ``Project.config["economics"]`` — the
    same override mechanism used for ``flow`` and ``forecast``.

    Args:
        session: Database session.
        project: The project to update.
        human_rate_usd_per_hour: New human hourly rate.
        error_cost_usd: New penalty per failed run.
        supervision_cost_usd: New flat supervision cost.

    Returns:
        The rates now in force.

    Raises:
        ValueError: A rate is negative or the human rate is zero.
    """
    if human_rate_usd_per_hour is not None and human_rate_usd_per_hour <= 0:
        raise ValueError("human_rate_usd_per_hour must be greater than zero")
    if error_cost_usd is not None and error_cost_usd < 0:
        raise ValueError("error_cost_usd must not be negative")
    if supervision_cost_usd is not None and supervision_cost_usd < 0:
        raise ValueError("supervision_cost_usd must not be negative")

    config = dict(project.config or {})
    economics = dict(config.get("economics") or {})
    if human_rate_usd_per_hour is not None:
        economics["human_rate_usd_per_hour"] = human_rate_usd_per_hour
    if error_cost_usd is not None:
        economics["error_cost_usd"] = error_cost_usd
    if supervision_cost_usd is not None:
        economics["supervision_cost_usd"] = supervision_cost_usd
    config["economics"] = economics
    project.config = config
    await session.flush()
    return economics


async def add_audit_note(
    session: AsyncSession,
    *,
    project_id: str,
    content: str,
    meta: dict[str, Any] | None = None,
    author: str = "mcp-client",
) -> Note:
    """Record a decision note about an economics change.

    Args:
        session: Database session.
        project_id: Project the note belongs to.
        content: Human-readable description of the change.
        meta: Structured details of the change.
        author: Who made the change.

    Returns:
        The stored note.
    """
    note = Note(
        project_id=project_id,
        kind=NoteKind.decision,
        content=content,
        author=author,
        meta=meta or {},
    )
    session.add(note)
    await session.flush()
    return note
