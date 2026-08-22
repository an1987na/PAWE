"""Add isolated legacy migration staging tables.

Revision ID: 20260803_0002
Revises: 20260803_0001
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0002"
down_revision: str | None = "20260803_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "legacy_migration_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_label", sa.String(length=64), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("source_file_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("manifest_hash"),
    )
    op.create_table(
        "legacy_documents_staging",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("document_type", sa.String(length=32), nullable=False),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column("rule_version", sa.String(length=32), nullable=True),
        sa.Column("linked_source_ref", sa.Text(), nullable=True),
        sa.Column("parse_quality", sa.String(length=20), nullable=False),
        sa.Column("verification_status", sa.String(length=24), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["legacy_migration_batches.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "source_ref", name="uq_legacy_document_source"),
    )
    op.create_index(
        "ix_legacy_documents_staging_batch_id",
        "legacy_documents_staging",
        ["batch_id"],
    )
    op.create_table(
        "legacy_items_staging",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("bucket", sa.String(length=16), nullable=False),
        sa.Column("stock_code", sa.String(length=6), nullable=False),
        sa.Column("stock_name", sa.String(length=64), nullable=False),
        sa.Column("direction", sa.String(length=120), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("baseline_price", sa.Numeric(precision=16, scale=6), nullable=True),
        sa.Column("target_return", sa.Numeric(precision=12, scale=8), nullable=True),
        sa.Column("week_high_return", sa.Numeric(precision=12, scale=8), nullable=True),
        sa.Column("close_return", sa.Numeric(precision=12, scale=8), nullable=True),
        sa.Column("max_drawdown", sa.Numeric(precision=12, scale=8), nullable=True),
        sa.Column("verification_status", sa.String(length=24), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["legacy_documents_staging.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "bucket",
            "stock_code",
            name="uq_legacy_item_document_bucket_stock",
        ),
    )
    op.create_index(
        "ix_legacy_items_staging_document_id",
        "legacy_items_staging",
        ["document_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_legacy_items_staging_document_id", table_name="legacy_items_staging")
    op.drop_table("legacy_items_staging")
    op.drop_index(
        "ix_legacy_documents_staging_batch_id",
        table_name="legacy_documents_staging",
    )
    op.drop_table("legacy_documents_staging")
    op.drop_table("legacy_migration_batches")
