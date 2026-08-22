"""Add cooperative job cancellation and checkpoints.

Revision ID: 20260813_0016
Revises: 20260813_0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0016"
down_revision: str | None = "20260813_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "checkpoint",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "jobs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("jobs", "cancel_requested_at")
    op.drop_column("jobs", "checkpoint")
