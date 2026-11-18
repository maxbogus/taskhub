"""add note external_ref

Revision ID: 7c1f9a2b4d6e
Revises: e0dc15e519d8
Create Date: 2026-11-18 02:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7c1f9a2b4d6e'
down_revision: str | None = 'e0dc15e519d8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("note") as batch_op:
        batch_op.add_column(
            sa.Column("external_ref", sa.String(length=1024), nullable=True)
        )
        batch_op.create_index(
            "uq_note_external_ref",
            ["project_id", "external_ref"],
            unique=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("note") as batch_op:
        batch_op.drop_index("uq_note_external_ref")
        batch_op.drop_column("external_ref")
