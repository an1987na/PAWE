"""Add isolated per-user watchlists and generated research outputs.

Revision ID: 20260812_0014
Revises: 20260810_0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260812_0014"
down_revision: str | None = "20260810_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_watchlist_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("stock_id", sa.BigInteger(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_watchlist_items_user_id", "user_watchlist_items", ["user_id"])
    op.create_index("ix_user_watchlist_items_stock_id", "user_watchlist_items", ["stock_id"])
    op.create_index(
        "ix_user_watchlist_user_active", "user_watchlist_items", ["user_id", "removed_at"]
    )
    op.create_index(
        "uq_user_watchlist_active_stock",
        "user_watchlist_items",
        ["user_id", "stock_id"],
        unique=True,
        postgresql_where=sa.text("removed_at IS NULL"),
    )
    op.create_table(
        "user_watchlist_daily_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("watchlist_item_id", sa.Uuid(), nullable=False),
        sa.Column("stock_id", sa.BigInteger(), nullable=False),
        sa.Column("week_id", sa.Date(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality", sa.String(20), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["watchlist_item_id"], ["user_watchlist_items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "trade_date", "stock_id", name="uq_watch_daily_item"),
    )
    op.create_index(
        "ix_watch_daily_user_week",
        "user_watchlist_daily_items",
        ["user_id", "week_id", "trade_date"],
    )
    op.create_table(
        "user_watchlist_weekly_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("watchlist_item_id", sa.Uuid(), nullable=False),
        sa.Column("stock_id", sa.BigInteger(), nullable=False),
        sa.Column("week_id", sa.Date(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality", sa.String(20), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["watchlist_item_id"], ["user_watchlist_items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "week_id", "stock_id", name="uq_watch_weekly_item"),
    )
    op.create_index(
        "ix_watch_weekly_user_week", "user_watchlist_weekly_items", ["user_id", "week_id"]
    )


def downgrade() -> None:
    op.drop_table("user_watchlist_weekly_items")
    op.drop_table("user_watchlist_daily_items")
    op.drop_table("user_watchlist_items")
