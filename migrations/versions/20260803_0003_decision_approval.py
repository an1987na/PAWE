"""Add candidate audit and approval persistence.

Revision ID: 20260803_0003
Revises: 20260803_0002
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0003"
down_revision: str | None = "20260803_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("decision_sets", sa.Column("source_decision_set_id", sa.Uuid(), nullable=True))
    op.add_column(
        "decision_sets",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_decision_sets_source_decision_set_id_decision_sets",
        "decision_sets",
        "decision_sets",
        ["source_decision_set_id"],
        ["id"],
    )
    op.create_table(
        "candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("week_id", sa.Date(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("stock_id", sa.BigInteger(), nullable=False),
        sa.Column("rule_score", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("bucket", sa.String(length=32), nullable=False),
        sa.Column("exclusion_reasons", sa.JSON(), nullable=False),
        sa.Column("score_breakdown", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["data_snapshots.id"]),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"]),
        sa.ForeignKeyConstraint(["week_id"], ["weeks.week_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("week_id", "stock_id", name="uq_candidate_week_stock"),
    )
    op.create_index("ix_candidates_week_id", "candidates", ["week_id"])
    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("week_id", sa.Date(), nullable=False),
        sa.Column("source_decision_set_id", sa.Uuid(), nullable=False),
        sa.Column("approved_decision_set_id", sa.Uuid(), nullable=True),
        sa.Column("decision_version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("selected_codes", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["approved_decision_set_id"], ["decision_sets.id"]),
        sa.ForeignKeyConstraint(["source_decision_set_id"], ["decision_sets.id"]),
        sa.ForeignKeyConstraint(["week_id"], ["weeks.week_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("week_id", "idempotency_key", name="uq_approval_idempotency"),
    )
    op.create_index("ix_approvals_week_id", "approvals", ["week_id"])
    op.create_table(
        "publication_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("week_id", sa.Date(), nullable=False),
        sa.Column("decision_set_id", sa.Uuid(), nullable=False),
        sa.Column("decision_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["decision_set_id"], ["decision_sets.id"]),
        sa.ForeignKeyConstraint(["week_id"], ["weeks.week_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("week_id", "idempotency_key", name="uq_publication_idempotency"),
    )
    op.create_index("ix_publication_events_week_id", "publication_events", ["week_id"])


def downgrade() -> None:
    op.drop_index("ix_publication_events_week_id", table_name="publication_events")
    op.drop_table("publication_events")
    op.drop_index("ix_approvals_week_id", table_name="approvals")
    op.drop_table("approvals")
    op.drop_index("ix_candidates_week_id", table_name="candidates")
    op.drop_table("candidates")
    op.drop_constraint(
        "fk_decision_sets_source_decision_set_id_decision_sets",
        "decision_sets",
        type_="foreignkey",
    )
    op.drop_column("decision_sets", "source_decision_set_id")
    op.drop_column("decision_sets", "published_at")
