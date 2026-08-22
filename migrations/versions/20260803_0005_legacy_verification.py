"""Add isolated legacy verification results.

Revision ID: 20260803_0005
Revises: 20260803_0004
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260803_0005"
down_revision: str | None = "20260803_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "legacy_items_staging", sa.Column("verification_source", sa.String(32), nullable=True)
    )
    op.add_column(
        "legacy_items_staging",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "legacy_items_staging",
        sa.Column("legacy_recalculated", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "legacy_items_staging",
        sa.Column("v9_recalculated", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "legacy_items_staging",
        sa.Column("verification_warnings", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("legacy_items_staging", "verification_warnings")
    op.drop_column("legacy_items_staging", "v9_recalculated")
    op.drop_column("legacy_items_staging", "legacy_recalculated")
    op.drop_column("legacy_items_staging", "verified_at")
    op.drop_column("legacy_items_staging", "verification_source")
