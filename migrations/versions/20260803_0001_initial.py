"""Initial weekly decision and daily brief schema.

Revision ID: 20260803_0001
Revises: None
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stocks",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("code", sa.String(length=6), nullable=False),
        sa.Column("exchange", sa.String(length=8), nullable=False),
        sa.Column("board", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("listing_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "exchange", name="uq_stocks_code_exchange"),
    )
    op.create_table(
        "data_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality", sa.String(length=20), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash"),
    )
    op.create_table(
        "weeks",
        sa.Column("week_id", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("market_state", sa.String(length=32), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["data_snapshots.id"]),
        sa.PrimaryKeyConstraint("week_id"),
    )
    op.create_table(
        "decision_sets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("week_id", sa.Date(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("shortage", sa.Boolean(), nullable=False),
        sa.Column("shortage_reason", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["week_id"], ["weeks.week_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("week_id", "type", "version", name="uq_decision_set_version"),
    )
    op.create_index(
        "ix_decision_sets_week_type_active",
        "decision_sets",
        ["week_id", "type", "is_active"],
    )
    op.create_table(
        "decision_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("decision_set_id", sa.Uuid(), nullable=False),
        sa.Column("stock_id", sa.BigInteger(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("target_return", sa.Numeric(precision=8, scale=6), nullable=False),
        sa.Column("confidence", sa.String(length=12), nullable=False),
        sa.Column("summary", sa.String(length=160), nullable=False),
        sa.Column("primary_risk", sa.String(length=120), nullable=False),
        sa.ForeignKeyConstraint(["decision_set_id"], ["decision_sets.id"]),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_set_id", "rank", name="uq_decision_item_rank"),
        sa.UniqueConstraint("decision_set_id", "stock_id", name="uq_decision_item_stock"),
    )
    op.create_table(
        "daily_briefs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("week_id", sa.Date(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("decision_set_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality", sa.String(length=20), nullable=False),
        sa.Column("ai_degraded", sa.Boolean(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["decision_set_id"], ["decision_sets.id"]),
        sa.ForeignKeyConstraint(["week_id"], ["weeks.week_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "week_id",
            "trade_date",
            "decision_set_id",
            "version",
            name="uq_daily_brief_version",
        ),
    )
    op.create_index(
        "ix_daily_briefs_week_date_active",
        "daily_briefs",
        ["week_id", "trade_date", "is_active"],
    )
    op.create_table(
        "daily_brief_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("daily_brief_id", sa.Uuid(), nullable=False),
        sa.Column("decision_item_id", sa.Uuid(), nullable=False),
        sa.Column("daily_return", sa.Numeric(precision=12, scale=8), nullable=False),
        sa.Column("week_to_date_return", sa.Numeric(precision=12, scale=8), nullable=False),
        sa.Column("week_high_return", sa.Numeric(precision=12, scale=8), nullable=False),
        sa.Column("drawdown_from_week_high", sa.Numeric(precision=12, scale=8), nullable=False),
        sa.Column("distance_to_target", sa.Numeric(precision=12, scale=8), nullable=False),
        sa.Column("volume_activity", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("risk_status", sa.String(length=24), nullable=False),
        sa.Column("summary", sa.String(length=320), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["daily_brief_id"], ["daily_briefs.id"]),
        sa.ForeignKeyConstraint(["decision_item_id"], ["decision_items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("daily_brief_id", "decision_item_id", name="uq_daily_brief_item"),
    )


def downgrade() -> None:
    op.drop_table("daily_brief_items")
    op.drop_index("ix_daily_briefs_week_date_active", table_name="daily_briefs")
    op.drop_table("daily_briefs")
    op.drop_table("decision_items")
    op.drop_index("ix_decision_sets_week_type_active", table_name="decision_sets")
    op.drop_table("decision_sets")
    op.drop_table("weeks")
    op.drop_table("data_snapshots")
    op.drop_table("stocks")
