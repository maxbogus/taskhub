"""add session project_id

Revision ID: e0dc15e519d8
Revises: 3f5e530692e5
Create Date: 2026-11-18 00:59:25.110631

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e0dc15e519d8"
down_revision: str | None = "3f5e530692e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("session") as batch_op:
        batch_op.add_column(sa.Column("project_id", sa.String(length=36), nullable=True))
        batch_op.create_index(batch_op.f("ix_session_project_id"), ["project_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_session_project_id",
            "project",
            ["project_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("session") as batch_op:
        batch_op.drop_constraint("fk_session_project_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_session_project_id"))
        batch_op.drop_column("project_id")
