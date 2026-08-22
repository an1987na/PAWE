"""Add encrypted per-user AI provider credentials.

Revision ID: 20260822_0019
Revises: 20260819_0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260822_0019"
down_revision: str | None = "20260819_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_ai_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("key_hint", sa.String(16), nullable=False),
        sa.Column("model", sa.String(96), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", name="uq_user_ai_credentials_user_id"),
    )
    op.create_index("ix_user_ai_credentials_user_id", "user_ai_credentials", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_ai_credentials_user_id", table_name="user_ai_credentials")
    op.drop_table("user_ai_credentials")
