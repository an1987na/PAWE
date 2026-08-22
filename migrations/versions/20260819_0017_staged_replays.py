"""Add isolated staged replay runs and replay-only output tables.

Revision ID: 20260819_0017
Revises: 20260813_0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260819_0017"
down_revision: str | None = "20260813_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("mode", sa.String(16), nullable=False, server_default=sa.text("'formal'")),
    )
    op.add_column("jobs", sa.Column("replay_stage", sa.String(24), nullable=True))
    op.add_column("jobs", sa.Column("trade_date", sa.Date(), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("replay_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    op.create_table(
        "replay_runs",
        _uuid(),
        sa.Column("week_id", sa.Date(), nullable=False),
        sa.Column("requested_stage", sa.String(24), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("effective_rule_version", sa.String(64), nullable=False),
        sa.Column("information_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("simulated_selection_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("simulated_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("simulated_trade_date", sa.Date(), nullable=True),
        sa.Column("actual_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "week_id",
            "requested_stage",
            "trade_date",
            "idempotency_key",
            name="uq_replay_run_idempotency",
        ),
    )
    op.create_index(
        "ix_replay_runs_week_stage", "replay_runs", ["week_id", "requested_stage", "status"]
    )

    op.create_table(
        "replay_stage_runs",
        _uuid(),
        sa.Column(
            "replay_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("replay_runs.id"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("information_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actual_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(240), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "replay_run_id", "stage", "trade_date", name="uq_replay_stage_run_target"
        ),
    )
    op.create_index("ix_replay_stage_runs_status", "replay_stage_runs", ["status", "stage"])

    op.create_table(
        "replay_decision_sets",
        _uuid(),
        sa.Column(
            "replay_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("replay_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "replay_stage_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("replay_stage_runs.id"),
            nullable=False,
        ),
        sa.Column("week_id", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("information_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "replay_stage_run_id", "version", name="uq_replay_decision_version"
        ),
    )
    op.create_index("ix_replay_decision_sets_run", "replay_decision_sets", ["replay_run_id"])
    op.create_index(
        "ix_replay_decision_sets_stage", "replay_decision_sets", ["replay_stage_run_id"]
    )

    op.create_table(
        "replay_decision_items",
        _uuid(),
        sa.Column(
            "replay_decision_set_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("replay_decision_sets.id"),
            nullable=False,
        ),
        sa.Column("stock_id", sa.BigInteger(), sa.ForeignKey("stocks.id"), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("target_return", sa.Numeric(8, 6), nullable=False),
        sa.Column("confidence", sa.String(12), nullable=False),
        sa.Column("summary", sa.String(160), nullable=False),
        sa.Column("primary_risk", sa.String(120), nullable=False),
        sa.UniqueConstraint(
            "replay_decision_set_id", "rank", name="uq_replay_decision_item_rank"
        ),
        sa.UniqueConstraint(
            "replay_decision_set_id", "stock_id", name="uq_replay_decision_item_stock"
        ),
    )
    op.create_index(
        "ix_replay_decision_items_set", "replay_decision_items", ["replay_decision_set_id"]
    )

    op.create_table(
        "replay_daily_briefs",
        _uuid(),
        sa.Column(
            "replay_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("replay_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "replay_stage_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("replay_stage_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "replay_decision_set_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("replay_decision_sets.id"),
            nullable=True,
        ),
        sa.Column("week_id", sa.Date(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality", sa.String(20), nullable=False),
        sa.Column("ai_degraded", sa.Boolean(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "replay_stage_run_id",
            "trade_date",
            "version",
            name="uq_replay_daily_brief_version",
        ),
    )
    op.create_index("ix_replay_daily_briefs_run", "replay_daily_briefs", ["replay_run_id"])

    op.create_table(
        "replay_daily_brief_items",
        _uuid(),
        sa.Column(
            "replay_daily_brief_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("replay_daily_briefs.id"),
            nullable=False,
        ),
        sa.Column("stock_id", sa.BigInteger(), sa.ForeignKey("stocks.id"), nullable=False),
        sa.Column("daily_return", sa.Numeric(12, 8), nullable=False),
        sa.Column("week_to_date_return", sa.Numeric(12, 8), nullable=False),
        sa.Column("week_high_return", sa.Numeric(12, 8), nullable=False),
        sa.Column("drawdown_from_week_high", sa.Numeric(12, 8), nullable=False),
        sa.Column("distance_to_target", sa.Numeric(12, 8), nullable=False),
        sa.Column("volume_activity", sa.Numeric(12, 6), nullable=True),
        sa.Column("risk_status", sa.String(24), nullable=False),
        sa.Column("summary", sa.String(320), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "replay_daily_brief_id", "stock_id", name="uq_replay_daily_brief_item_stock"
        ),
    )
    op.create_index(
        "ix_replay_daily_brief_items_brief", "replay_daily_brief_items", ["replay_daily_brief_id"]
    )

    op.create_table(
        "replay_weekly_reviews",
        _uuid(),
        sa.Column(
            "replay_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("replay_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "replay_stage_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("replay_stage_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "replay_decision_set_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("replay_decision_sets.id"),
            nullable=True,
        ),
        sa.Column("week_id", sa.Date(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("entry_trade_date", sa.Date(), nullable=False),
        sa.Column("final_trade_date", sa.Date(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality", sa.String(20), nullable=False),
        sa.Column("aggregate", postgresql.JSONB(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.UniqueConstraint("replay_stage_run_id", name="uq_replay_weekly_review_stage"),
    )
    op.create_index("ix_replay_weekly_reviews_run", "replay_weekly_reviews", ["replay_run_id"])

    op.create_table(
        "replay_weekly_review_items",
        _uuid(),
        sa.Column(
            "replay_weekly_review_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("replay_weekly_reviews.id"),
            nullable=False,
        ),
        sa.Column("stock_id", sa.BigInteger(), sa.ForeignKey("stocks.id"), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("week_high_return", sa.Numeric(12, 8), nullable=False),
        sa.Column("week_close_return", sa.Numeric(12, 8), nullable=False),
        sa.Column("max_drawdown_from_entry", sa.Numeric(12, 8), nullable=False),
        sa.Column("max_peak_to_trough_drawdown", sa.Numeric(12, 8), nullable=False),
        sa.Column("target_touched", sa.Boolean(), nullable=False),
        sa.Column("target_touch_date", sa.Date(), nullable=True),
        sa.Column("drawdown_before_touch", sa.Numeric(12, 8), nullable=True),
        sa.Column("accessible_at_entry", sa.Boolean(), nullable=False),
        sa.Column("benchmark_return", sa.Numeric(12, 8), nullable=True),
        sa.Column("benchmark_excess", sa.Numeric(12, 8), nullable=True),
        sa.Column("industry_return", sa.Numeric(12, 8), nullable=True),
        sa.Column("industry_excess", sa.Numeric(12, 8), nullable=True),
        sa.UniqueConstraint(
            "replay_weekly_review_id", "stock_id", name="uq_replay_weekly_review_item_stock"
        ),
    )
    op.create_index(
        "ix_replay_weekly_review_items_review",
        "replay_weekly_review_items",
        ["replay_weekly_review_id"],
    )
    op.create_foreign_key(
        "fk_jobs_replay_run_id",
        "jobs",
        "replay_runs",
        ["replay_run_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_jobs_replay_run_id", "jobs", type_="foreignkey")
    for table in (
        "replay_weekly_review_items",
        "replay_weekly_reviews",
        "replay_daily_brief_items",
        "replay_daily_briefs",
        "replay_decision_items",
        "replay_decision_sets",
        "replay_stage_runs",
        "replay_runs",
    ):
        op.drop_table(table)
    op.drop_column("jobs", "replay_run_id")
    op.drop_column("jobs", "trade_date")
    op.drop_column("jobs", "replay_stage")
    op.drop_column("jobs", "mode")
