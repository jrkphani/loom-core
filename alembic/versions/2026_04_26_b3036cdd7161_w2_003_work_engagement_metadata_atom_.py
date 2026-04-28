"""W2 003: work_engagement_metadata + atom extensions

Revision ID: b3036cdd7161
Revises: 6ee38e331de0
Create Date: 2026-04-26 22:04:27.175389

Hand-reviewed against loom-meta/docs/loom-schema-v1.sql Section 4 (lines ~432-480).
Autogenerate also emitted false-positive drop/recreate operations for expression-based
indexes — those were removed; only the three table creations are real new work here.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3036cdd7161"
down_revision: str | None = "6ee38e331de0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_ask_side",
        sa.Column("atom_id", sa.String(length=26), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "side IN ('asks_of_aws', 'asks_of_customer', 'asks_of_1cloudhub')",
            name="ck_was_side",
        ),
        sa.ForeignKeyConstraint(["atom_id"], ["atoms.id"]),
        sa.PrimaryKeyConstraint("atom_id"),
    )
    with op.batch_alter_table("work_ask_side", schema=None) as batch_op:
        batch_op.create_index("idx_was_side", ["side"], unique=False)

    op.create_table(
        "work_commitment_direction",
        sa.Column("atom_id", sa.String(length=26), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "direction IN ('1ch_to_customer', 'customer_to_1ch',"
            " '1ch_to_aws', 'aws_to_1ch',"
            " 'customer_to_aws', 'aws_to_customer', '1ch_internal')",
            name="ck_wcd_direction",
        ),
        sa.ForeignKeyConstraint(["atom_id"], ["atoms.id"]),
        sa.PrimaryKeyConstraint("atom_id"),
    )
    with op.batch_alter_table("work_commitment_direction", schema=None) as batch_op:
        batch_op.create_index("idx_wcd_direction", ["direction"], unique=False)

    op.create_table(
        "work_engagement_metadata",
        sa.Column("engagement_id", sa.String(length=26), nullable=False),
        sa.Column("sow_value", sa.Float(), nullable=True),
        sa.Column("sow_currency", sa.Text(), nullable=True),
        sa.Column("aws_funded", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("aws_program", sa.Text(), nullable=True),
        sa.Column("swim_lane", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "swim_lane IN ('p1_existing_customer', 'p2_sales_generated',"
            " 'p3_demand_gen_sdr', 'p4_aws_referral')",
            name="ck_wem_swim_lane",
        ),
        sa.ForeignKeyConstraint(["engagement_id"], ["engagements.id"]),
        sa.PrimaryKeyConstraint("engagement_id"),
    )


def downgrade() -> None:
    op.drop_table("work_engagement_metadata")
    with op.batch_alter_table("work_commitment_direction", schema=None) as batch_op:
        batch_op.drop_index("idx_wcd_direction")
    op.drop_table("work_commitment_direction")
    with op.batch_alter_table("work_ask_side", schema=None) as batch_op:
        batch_op.drop_index("idx_was_side")
    op.drop_table("work_ask_side")
