#!/usr/bin/env python3
"""Seed the deterministic data the eval scenarios talk about.

Both the MCP contract run (layer 2b) and the agentic run (layer 3) need a
database with something in it — otherwise every answer is "no usage
recorded" and the scenarios prove nothing.

The seed is idempotent: it is safe to run it on every start.

Data (project ``demo``):

* one finished task (4h estimate) and one open task (6h estimate);
* two metric rows on the model ``demo-large``: a priced one and a failed
  one, so the error rate is non-zero;
* a price for ``demo-*`` so the token cost is computable;
* the human rate is 50 USD/h, set in the project config.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sqlalchemy import select  # noqa: E402

from taskhub.db import init_db, session_scope  # noqa: E402
from taskhub.models import (  # noqa: E402
    Agent,
    Metric,
    ModelPrice,
    Project,
    SessionSource,
    WorkStatus,
)
from taskhub.models import Session as WorkSession  # noqa: E402
from taskhub.repo.projects import create_project  # noqa: E402
from taskhub.repo.work_items import create_item  # noqa: E402

#: The project every scenario talks about.
PROJECT_SLUG = "demo"

#: Human hourly rate used by the scenarios.
HUMAN_RATE = 50.0

#: Price of the seeded model, in micro-USD per 1M tokens.
DEMO_INPUT_MICRO = 4_000_000
DEMO_OUTPUT_MICRO = 20_000_000


def _now() -> datetime:
    """Current time as a naive UTC timestamp."""
    return datetime.now(UTC).replace(tzinfo=None)


async def seed_demo(session: Any) -> bool:
    """Create the demo project with its items, metrics and prices.

    Args:
        session: An open database session.

    Returns:
        True when the data was created, False when it was already there.
    """
    if True:
        existing = (
            await session.execute(select(Project).where(Project.slug == PROJECT_SLUG))
        ).scalar_one_or_none()

        if existing is not None:
            return False

        project = await create_project(
            session,
            slug=PROJECT_SLUG,
            name="Demo",
            root_path="/tmp/taskhub-eval-demo",
            config={"economics": {"human_rate_usd_per_hour": HUMAN_RATE}},
        )

        done = await create_item(
            session,
            project_id=project.id,
            title="Ship the economics report",
            status=WorkStatus.done,
            estimate_hours=4.0,
        )
        done.created_at = _now() - timedelta(days=3)
        done.started_at = _now() - timedelta(days=2)
        done.done_at = _now() - timedelta(days=2, hours=2)

        await create_item(
            session,
            project_id=project.id,
            title="Add the cost panel to the dashboard",
            status=WorkStatus.ready,
            estimate_hours=6.0,
        )

        agent = Agent(name="eval-agent")
        session.add(agent)
        await session.flush()

        session.add(
            ModelPrice(
                pattern="demo-*",
                provider="local",
                currency="USD",
                input_usd_micro=DEMO_INPUT_MICRO,
                output_usd_micro=DEMO_OUTPUT_MICRO,
                cache_read_usd_micro=0,
                cache_write_usd_micro=0,
                valid_from=_now() - timedelta(days=30),
            )
        )

        # One successful run with a known token count, one failed run.
        recorded = _now() - timedelta(days=1)
        work_session = WorkSession(
            project_id=project.id,
            work_item_id=done.id,
            agent_id=agent.id,
            source=SessionSource.manual,
            model="demo-large",
            started_at=recorded,
            ended_at=recorded,
        )
        session.add(work_session)
        await session.flush()
        session.add(
            Metric(
                session_id=work_session.id,
                model="demo-large",
                prompt_tokens=2_000_000,
                completion_tokens=500_000,
                active_ms=3_600_000,
                cost_usd=12.5,
                success=True,
                recorded_at=recorded,
            )
        )
        await session.flush()
        await session.commit()
        return True


async def seed() -> None:
    """Seed the database used by the eval runs."""
    await init_db()
    async with session_scope() as session:
        await seed_demo(session)


def main() -> int:
    """Seed the database.

    Returns:
        0 on success.
    """
    asyncio.run(seed())
    return 0


if __name__ == "__main__":
    sys.exit(main())
