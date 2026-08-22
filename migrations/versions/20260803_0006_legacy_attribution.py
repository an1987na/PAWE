"""Add legacy conflict attribution and replay eligibility.

Revision ID: 20260803_0006
Revises: 20260803_0005
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260803_0006"
down_revision: str | None = "20260803_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "legacy_items_staging",
        sa.Column("conflict_attribution", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "legacy_items_staging",
        sa.Column("replay_eligibility", sa.String(32), nullable=True),
    )
    op.add_column(
        "legacy_items_staging",
        sa.Column("replay_arm", sa.String(24), nullable=True),
    )
    op.create_index(
        "ix_legacy_items_staging_replay_eligibility",
        "legacy_items_staging",
        ["replay_eligibility"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_legacy_items_staging_replay_eligibility",
        table_name="legacy_items_staging",
    )
    op.drop_column("legacy_items_staging", "replay_arm")
    op.drop_column("legacy_items_staging", "replay_eligibility")
    op.drop_column("legacy_items_staging", "conflict_attribution")
