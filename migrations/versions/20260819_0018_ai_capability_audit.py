"""Add capability-scoped AI invocations, audits and attribution records.

Revision ID: 20260819_0018
Revises: 20260819_0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260819_0018"
down_revision: str | None = "20260819_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def upgrade() -> None:
    op.create_table(
        "ai_invocations",
        _uuid(),
        sa.Column("capability", sa.String(32), nullable=False),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.String(96), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(96), nullable=False),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("context", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("structured_input", postgresql.JSONB(), nullable=False),
        sa.Column("structured_output", postgresql.JSONB(), nullable=True),
        sa.Column("usage", postgresql.JSONB(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(240), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_ai_invocations_capability_created", "ai_invocations", ["capability", "created_at"]
    )
    op.create_index("ix_ai_invocations_subject", "ai_invocations", ["subject_type", "subject_id"])

    op.create_table(
        "ai_audits",
        _uuid(),
        sa.Column(
            "invocation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_invocations.id"),
            nullable=False,
        ),
        sa.Column("capability", sa.String(32), nullable=False),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.String(96), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("output_hash", sa.String(64), nullable=True),
        sa.Column("validation", postgresql.JSONB(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_audits_capability_created", "ai_audits", ["capability", "created_at"])
    op.create_index("ix_ai_audits_invocation_id", "ai_audits", ["invocation_id"])

    op.create_table(
        "ai_candidate_analyses",
        _uuid(),
        sa.Column(
            "invocation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_invocations.id"),
            nullable=False,
        ),
        sa.Column(
            "replay_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("replay_runs.id"),
            nullable=True,
        ),
        sa.Column("week_id", sa.Date(), nullable=False),
        sa.Column("stock_id", sa.BigInteger(), sa.ForeignKey("stocks.id"), nullable=False),
        sa.Column("adjustment", sa.Integer(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("invocation_id", "stock_id", name="uq_ai_candidate_analysis_stock"),
    )
    op.create_index("ix_ai_candidate_analyses_replay", "ai_candidate_analyses", ["replay_run_id"])
    op.create_index("ix_ai_candidate_analyses_week", "ai_candidate_analyses", ["week_id"])

    op.create_table(
        "error_attributions",
        _uuid(),
        sa.Column("week_id", sa.Date(), nullable=False),
        sa.Column(
            "review_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("weekly_reviews.id"),
            nullable=True,
        ),
        sa.Column(
            "invocation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_invocations.id"),
            nullable=True,
        ),
        sa.Column("taxonomy", sa.String(48), nullable=False),
        sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column("facts", postgresql.JSONB(), nullable=False),
        sa.Column("proposed_hypothesis", sa.Text(), nullable=False),
        sa.Column("counterfactual_allowed", sa.Boolean(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_error_attributions_week_status", "error_attributions", ["week_id", "status"]
    )

    op.create_table(
        "attribution_resolutions",
        _uuid(),
        sa.Column(
            "attribution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("error_attributions.id"),
            nullable=False,
        ),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_attribution_resolutions_attribution", "attribution_resolutions", ["attribution_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_attribution_resolutions_attribution", table_name="attribution_resolutions")
    op.drop_table("attribution_resolutions")
    op.drop_index("ix_error_attributions_week_status", table_name="error_attributions")
    op.drop_table("error_attributions")
    op.drop_index("ix_ai_candidate_analyses_week", table_name="ai_candidate_analyses")
    op.drop_index("ix_ai_candidate_analyses_replay", table_name="ai_candidate_analyses")
    op.drop_table("ai_candidate_analyses")
    op.drop_index("ix_ai_audits_capability_created", table_name="ai_audits")
    op.drop_index("ix_ai_audits_invocation_id", table_name="ai_audits")
    op.drop_table("ai_audits")
    op.drop_index("ix_ai_invocations_subject", table_name="ai_invocations")
    op.drop_index("ix_ai_invocations_capability_created", table_name="ai_invocations")
    op.drop_table("ai_invocations")
