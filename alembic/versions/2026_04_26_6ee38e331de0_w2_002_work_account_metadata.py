"""W2 002: work_account_metadata

Revision ID: 6ee38e331de0
Revises: bf4d89061130
Create Date: 2026-04-26 21:50:38.035196

Hand-reviewed against loom-meta/docs/loom-schema-v1.sql § Section 4 (line ~423).
Autogenerate also emitted false-positive drop/recreate operations for expression-
based indexes (DESC columns, COLLATE NOCASE) that SQLAlchemy 2.0.49 cannot reflect
from SQLite — those were removed; only the table creation is real new work here.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6ee38e331de0"
down_revision: str | None = "bf4d89061130"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_account_metadata",
        sa.Column("arena_id", sa.String(length=26), nullable=False),
        sa.Column("industry", sa.Text(), nullable=True),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("aws_segment", sa.Text(), nullable=True),
        sa.Column("customer_type", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["arena_id"],
            ["arenas.id"],
        ),
        sa.PrimaryKeyConstraint("arena_id"),
    )


def downgrade() -> None:
    op.drop_table("work_account_metadata")
