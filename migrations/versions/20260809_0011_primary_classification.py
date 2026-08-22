"""Constrain the single active PAWE primary classification.

Revision ID: 20260809_0011
Revises: 20260809_0010
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_0011"
down_revision: str | None = "20260809_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "stock_classifications", sa.Column("published_at", sa.Date(), nullable=True)
    )
    op.add_column(
        "stock_classifications", sa.Column("evidence_url", sa.String(512), nullable=True)
    )
    op.create_check_constraint(
        "ck_stock_classification_primary_shape",
        "stock_classifications",
        "NOT is_primary OR "
        "(classification_type = 'pawe_primary' AND domain IS NOT NULL "
        "AND sector_code IS NOT NULL)",
    )
    op.create_index(
        "uq_stock_classification_active_primary",
        "stock_classifications",
        ["stock_id"],
        unique=True,
        postgresql_where=sa.text("is_primary AND valid_to IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_stock_classification_active_primary",
        table_name="stock_classifications",
    )
    op.drop_constraint(
        "ck_stock_classification_primary_shape",
        "stock_classifications",
        type_="check",
    )
    op.drop_column("stock_classifications", "evidence_url")
    op.drop_column("stock_classifications", "published_at")
