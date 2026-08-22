"""Add trading calendar and versioned V9 inputs.

Revision ID: 20260803_0008
Revises: 20260803_0007
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260803_0008"
down_revision: str | None = "20260803_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trading_calendar",
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("is_open", sa.Boolean(), nullable=False),
        sa.Column("previous_open_date", sa.Date(), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("quality", sa.String(20), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("trade_date"),
    )
    op.create_table(
        "weekly_features",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("stock_id", sa.BigInteger(), nullable=False),
        sa.Column("feature_version", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["data_snapshots.id"]),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id", "stock_id", "feature_version", name="uq_weekly_feature_version"
        ),
    )
    op.create_index("ix_weekly_features_snapshot_id", "weekly_features", ["snapshot_id"])
    op.create_index("ix_weekly_features_stock_id", "weekly_features", ["stock_id"])
    op.create_table(
        "weekly_state_inputs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("input_version", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["data_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "input_version", name="uq_weekly_state_input_version"),
    )
    op.create_index(
        "ix_weekly_state_inputs_snapshot_id", "weekly_state_inputs", ["snapshot_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_weekly_state_inputs_snapshot_id", table_name="weekly_state_inputs")
    op.drop_table("weekly_state_inputs")
    op.drop_index("ix_weekly_features_stock_id", table_name="weekly_features")
    op.drop_index("ix_weekly_features_snapshot_id", table_name="weekly_features")
    op.drop_table("weekly_features")
    op.drop_table("trading_calendar")
