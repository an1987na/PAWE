"""Add stock metadata, classifications, and provider daily bars.

Revision ID: 20260809_0010
Revises: 20260809_0009
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_0010"
down_revision: str | None = "20260809_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("stocks", sa.Column("source", sa.String(32), nullable=True))
    op.add_column("stocks", sa.Column("quality", sa.String(20), nullable=True))
    op.add_column("stocks", sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("stocks", sa.Column("content_hash", sa.String(64), nullable=True))
    op.add_column("stocks", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "stock_classifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("stock_id", sa.BigInteger(), nullable=False),
        sa.Column("classification_type", sa.String(32), nullable=False),
        sa.Column("label", sa.String(96), nullable=False),
        sa.Column("domain", sa.String(20), nullable=True),
        sa.Column("sector_code", sa.String(64), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("quality", sa.String(20), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "stock_id",
            "classification_type",
            "source",
            "valid_from",
            name="uq_stock_classification_version",
        ),
    )
    op.create_index(
        "ix_stock_classifications_stock_id", "stock_classifications", ["stock_id"]
    )
    op.create_table(
        "daily_bars",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("stock_id", sa.BigInteger(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(18, 6), nullable=False),
        sa.Column("high", sa.Numeric(18, 6), nullable=False),
        sa.Column("low", sa.Numeric(18, 6), nullable=False),
        sa.Column("close", sa.Numeric(18, 6), nullable=False),
        sa.Column("volume", sa.Numeric(24, 4), nullable=False),
        sa.Column("amount", sa.Numeric(24, 4), nullable=True),
        sa.Column("adjustment", sa.String(16), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("quality", sa.String(20), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "stock_id",
            "trade_date",
            "adjustment",
            "source",
            "content_hash",
            name="uq_daily_bar_version",
        ),
    )
    op.create_index("ix_daily_bars_stock_id", "daily_bars", ["stock_id"])
    op.create_index("ix_daily_bars_trade_date", "daily_bars", ["trade_date"])


def downgrade() -> None:
    op.drop_index("ix_daily_bars_trade_date", table_name="daily_bars")
    op.drop_index("ix_daily_bars_stock_id", table_name="daily_bars")
    op.drop_table("daily_bars")
    op.drop_index("ix_stock_classifications_stock_id", table_name="stock_classifications")
    op.drop_table("stock_classifications")
    op.drop_column("stocks", "last_seen_at")
    op.drop_column("stocks", "content_hash")
    op.drop_column("stocks", "fetched_at")
    op.drop_column("stocks", "quality")
    op.drop_column("stocks", "source")
