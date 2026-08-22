"""Add historical replay and weekly review results.

Revision ID: 20260810_0013
Revises: 20260809_0012
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260810_0013"
down_revision: str | None = "20260809_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "historical_replays",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("week_id", sa.Date(), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("decision_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("simulated_selection_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("simulated_review_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actual_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality", sa.String(20), nullable=False),
        sa.Column("selected_codes", sa.JSON(), nullable=False),
        sa.Column("decision_payload", postgresql.JSONB(), nullable=False),
        sa.Column("daily_briefs_payload", postgresql.JSONB(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash"),
        sa.UniqueConstraint(
            "week_id", "rule_version", name="uq_historical_replay_week_rule"
        ),
    )
    op.create_index("ix_historical_replays_week_id", "historical_replays", ["week_id"])
    op.create_table(
        "weekly_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("week_id", sa.Date(), nullable=False),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("decision_set_id", sa.Uuid(), nullable=True),
        sa.Column("replay_run_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("entry_trade_date", sa.Date(), nullable=False),
        sa.Column("final_trade_date", sa.Date(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality", sa.String(20), nullable=False),
        sa.Column("aggregate", postgresql.JSONB(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("report_markdown", sa.Text(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["decision_set_id"], ["decision_sets.id"]),
        sa.ForeignKeyConstraint(["replay_run_id"], ["historical_replays.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "week_id",
            "source_type",
            "source_version",
            "rule_version",
            name="uq_weekly_review_source",
        ),
    )
    op.create_index("ix_weekly_reviews_week_id", "weekly_reviews", ["week_id"])
    op.create_index(
        "ix_weekly_reviews_week_active", "weekly_reviews", ["week_id", "is_active"]
    )
    op.create_table(
        "weekly_review_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("weekly_review_id", sa.Uuid(), nullable=False),
        sa.Column("stock_id", sa.BigInteger(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("week_high_return", sa.Numeric(12, 8), nullable=False),
        sa.Column("week_close_return", sa.Numeric(12, 8), nullable=False),
        sa.Column("max_drawdown_from_entry", sa.Numeric(12, 8), nullable=False),
        sa.Column("max_peak_to_trough_drawdown", sa.Numeric(12, 8), nullable=False),
        sa.Column("target_touched", sa.Boolean(), nullable=False),
        sa.Column("target_touch_date", sa.Date(), nullable=True),
        sa.Column("drawdown_before_touch", sa.Numeric(12, 8), nullable=True),
        sa.Column("accessible_at_entry", sa.Boolean(), nullable=False),
        sa.Column("benchmark_return", sa.Numeric(12, 8), nullable=True),
        sa.Column("benchmark_excess", sa.Numeric(12, 8), nullable=True),
        sa.Column("industry_return", sa.Numeric(12, 8), nullable=True),
        sa.Column("industry_excess", sa.Numeric(12, 8), nullable=True),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"]),
        sa.ForeignKeyConstraint(["weekly_review_id"], ["weekly_reviews.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "weekly_review_id", "stock_id", name="uq_weekly_review_item"
        ),
    )
    op.create_index(
        "ix_weekly_review_items_weekly_review_id",
        "weekly_review_items",
        ["weekly_review_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_weekly_review_items_weekly_review_id", table_name="weekly_review_items"
    )
    op.drop_table("weekly_review_items")
    op.drop_index("ix_weekly_reviews_week_active", table_name="weekly_reviews")
    op.drop_index("ix_weekly_reviews_week_id", table_name="weekly_reviews")
    op.drop_table("weekly_reviews")
    op.drop_index("ix_historical_replays_week_id", table_name="historical_replays")
    op.drop_table("historical_replays")
