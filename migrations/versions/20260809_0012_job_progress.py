"""Prevent duplicate active weekly jobs.

Revision ID: 20260809_0012
Revises: 20260809_0011
Create Date: 2026-08-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260809_0012"
down_revision: str | None = "20260809_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_jobs_active_weekly_selection",
        "jobs",
        ["week_id"],
        unique=True,
        postgresql_where=(
            "job_type = 'weekly_selection' AND status IN ('queued', 'running')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_jobs_active_weekly_selection", table_name="jobs")
