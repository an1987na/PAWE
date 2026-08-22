"""Add replayable snapshot records.

Revision ID: 20260809_0009
Revises: 20260803_0008
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260809_0009"
down_revision: str | None = "20260803_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_snapshot_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("record_key", sa.String(96), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.String(40), nullable=True),
        sa.Column("adjustment", sa.String(16), nullable=True),
        sa.Column("quality", sa.String(20), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["data_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id",
            "record_key",
            "source",
            name="uq_data_snapshot_record_source",
        ),
    )
    op.create_index(
        "ix_data_snapshot_records_snapshot_id",
        "data_snapshot_records",
        ["snapshot_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_data_snapshot_records_snapshot_id",
        table_name="data_snapshot_records",
    )
    op.drop_table("data_snapshot_records")
