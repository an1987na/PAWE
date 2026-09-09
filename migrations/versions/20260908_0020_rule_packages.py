"""Add immutable uploaded rule package records; no formal rule activation.

Revision ID: 20260908_0020
Revises: 20260822_0019
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260908_0020"
down_revision = "20260822_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rule_packages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.String(64), nullable=False, unique=True),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("validation", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("rule_packages")
