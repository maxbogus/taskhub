"""add model_price

Revision ID: b2f4c8e1a903
Revises: 7c1f9a2b4d6e
Create Date: 2026-11-22 16:40:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2f4c8e1a903"
down_revision: str | None = "7c1f9a2b4d6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_price",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("pattern", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=120), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("input_usd_micro", sa.Integer(), nullable=False),
        sa.Column("output_usd_micro", sa.Integer(), nullable=False),
        sa.Column("cache_read_usd_micro", sa.Integer(), nullable=False),
        sa.Column("cache_write_usd_micro", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=False),
        sa.Column("valid_to", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pattern", "valid_from", name="uq_model_price_pattern_valid_from"),
    )
    op.create_index("ix_model_price_pattern", "model_price", ["pattern"])


def downgrade() -> None:
    op.drop_index("ix_model_price_pattern", table_name="model_price")
    op.drop_table("model_price")
