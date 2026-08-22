"""Add experiment governance, source capabilities, and feature artifacts.

Revision ID: 20260813_0015
Revises: 20260812_0014
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0015"
down_revision: str | None = "20260812_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_capabilities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.String(32), nullable=False),
        sa.Column("adapter_version", sa.String(32), nullable=False),
        sa.Column("dataset", sa.String(48), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(), nullable=False),
        sa.Column("market_coverage", postgresql.JSONB(), nullable=False),
        sa.Column("time_semantics", postgresql.JSONB(), nullable=False),
        sa.Column("auth_mode", sa.String(24), nullable=False),
        sa.Column("terms_reviewed_at", sa.Date(), nullable=True),
        sa.Column("formal_eligibility", sa.String(20), nullable=False),
        sa.Column("fallback_priority", sa.Integer(), nullable=False),
        sa.Column("policy", postgresql.JSONB(), nullable=False),
        sa.Column("quality", sa.String(20), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_reason", sa.String(240), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "formal_eligibility IN ('formal', 'research_only', 'disabled')",
            name="ck_source_capability_eligibility",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id", "adapter_version", "dataset", name="uq_source_capability_version"
        ),
    )
    op.create_index("ix_source_capabilities_source_id", "source_capabilities", ["source_id"])
    op.create_index("ix_source_capabilities_dataset", "source_capabilities", ["dataset"])
    capability_table = sa.table(
        "source_capabilities",
        sa.column("id", sa.Uuid()),
        sa.column("source_id", sa.String()),
        sa.column("adapter_version", sa.String()),
        sa.column("dataset", sa.String()),
        sa.column("capabilities", postgresql.JSONB()),
        sa.column("market_coverage", postgresql.JSONB()),
        sa.column("time_semantics", postgresql.JSONB()),
        sa.column("auth_mode", sa.String()),
        sa.column("terms_reviewed_at", sa.Date()),
        sa.column("formal_eligibility", sa.String()),
        sa.column("fallback_priority", sa.Integer()),
        sa.column("policy", postgresql.JSONB()),
        sa.column("quality", sa.String()),
        sa.column("last_success_at", sa.DateTime(timezone=True)),
        sa.column("last_failure_at", sa.DateTime(timezone=True)),
        sa.column("last_failure_reason", sa.String()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    reviewed_at = date(2026, 8, 13)
    registered_at = datetime(2026, 8, 13, tzinfo=UTC)
    op.bulk_insert(
        capability_table,
        [
            _capability_row(
                "sse",
                "official-v1",
                "stock_master",
                ["SSE"],
                "formal",
                1,
                reviewed_at,
                registered_at,
            ),
            _capability_row(
                "szse",
                "official-v1",
                "stock_master",
                ["SZSE"],
                "formal",
                1,
                reviewed_at,
                registered_at,
            ),
            _capability_row(
                "tencent",
                "qfqday-v1",
                "qfq_daily_bars",
                ["SSE", "SZSE"],
                "formal",
                1,
                reviewed_at,
                registered_at,
            ),
            _capability_row(
                "eastmoney",
                "kline-v1",
                "qfq_daily_bars",
                ["SSE", "SZSE"],
                "research_only",
                2,
                reviewed_at,
                registered_at,
            ),
            _capability_row(
                "sina",
                "akshare-v1",
                "qfq_daily_bars",
                ["SSE", "SZSE"],
                "research_only",
                3,
                reviewed_at,
                registered_at,
            ),
        ],
    )

    op.create_table(
        "source_mapping_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.String(32), nullable=False),
        sa.Column("dataset", sa.String(48), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("mapping", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("validation_result", postgresql.JSONB(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'validated', 'approved', 'rejected', 'superseded')",
            name="ck_source_mapping_status",
        ),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "dataset", "version", name="uq_source_mapping_version"),
    )
    op.create_index(
        "ix_source_mapping_versions_source_id", "source_mapping_versions", ["source_id"]
    )
    op.create_index("ix_source_mapping_versions_dataset", "source_mapping_versions", ["dataset"])

    op.create_table(
        "feature_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("partition_key", sa.String(96), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("feature_version", sa.String(32), nullable=False),
        sa.Column("code_version", sa.String(64), nullable=False),
        sa.Column("decision_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_hashes", postgresql.JSONB(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("quality", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("uri", sa.Text(), nullable=True),
        sa.Column("build_job_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('building', 'published', 'failed', 'cancelled')",
            name="ck_feature_artifact_status",
        ),
        sa.ForeignKeyConstraint(["snapshot_id"], ["data_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id",
            "partition_key",
            "schema_version",
            "feature_version",
            "code_version",
            name="uq_feature_artifact_build",
        ),
    )
    op.create_index("ix_feature_artifacts_snapshot_id", "feature_artifacts", ["snapshot_id"])
    op.create_index("ix_feature_artifacts_status", "feature_artifacts", ["status"])

    op.create_table(
        "rule_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("proposal_key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("base_rule_version", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(24), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("dsl", postgresql.JSONB(), nullable=False),
        sa.Column("objectives", postgresql.JSONB(), nullable=False),
        sa.Column("required_features", postgresql.JSONB(), nullable=False),
        sa.Column("invalidation_conditions", postgresql.JSONB(), nullable=False),
        sa.Column("rollback_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("validation_result", postgresql.JSONB(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('proposed', 'schema_validated', 'invalid')",
            name="ck_rule_proposal_status",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proposal_key", name="uq_rule_proposal_key"),
    )
    op.create_index("ix_rule_proposals_status", "rule_proposals", ["status"])

    op.create_table(
        "experiments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rule_proposal_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("baseline_rule_version", sa.String(64), nullable=False),
        sa.Column("candidate_rule_version", sa.String(64), nullable=False),
        sa.Column("rollback_version", sa.String(64), nullable=False),
        sa.Column("activated_rule_version", sa.String(64), nullable=True),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["rule_proposal_id"], ["rule_proposals.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_proposal_id", name="uq_experiment_rule_proposal"),
    )
    op.create_index("ix_experiments_rule_proposal_id", "experiments", ["rule_proposal_id"])
    op.create_index("ix_experiments_status", "experiments", ["status"])

    op.create_table(
        "experiment_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("run_type", sa.String(16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("run_type IN ('replay', 'shadow')", name="ck_experiment_run_type"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("experiment_id", "run_type", "attempt", name="uq_experiment_run"),
    )
    op.create_index("ix_experiment_runs_experiment_id", "experiment_runs", ["experiment_id"])
    op.create_index("ix_experiment_runs_status", "experiment_runs", ["status"])

    op.create_table(
        "experiment_folds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("fold_index", sa.Integer(), nullable=False),
        sa.Column("train_start", sa.Date(), nullable=False),
        sa.Column("train_end", sa.Date(), nullable=False),
        sa.Column("selection_start", sa.Date(), nullable=False),
        sa.Column("selection_end", sa.Date(), nullable=False),
        sa.Column("validation_start", sa.Date(), nullable=False),
        sa.Column("validation_end", sa.Date(), nullable=False),
        sa.Column("snapshot_ids", postgresql.JSONB(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("capacity_distribution", postgresql.JSONB(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("integrity_status", sa.String(24), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["experiment_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "fold_index", name="uq_experiment_fold"),
    )
    op.create_index("ix_experiment_folds_run_id", "experiment_folds", ["run_id"])

    op.create_table(
        "experiment_approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "experiment_id", "experiment_version", name="uq_experiment_approval"
        ),
    )
    op.create_index(
        "ix_experiment_approvals_experiment_id", "experiment_approvals", ["experiment_id"]
    )


def downgrade() -> None:
    op.drop_table("experiment_approvals")
    op.drop_table("experiment_folds")
    op.drop_table("experiment_runs")
    op.drop_table("experiments")
    op.drop_table("rule_proposals")
    op.drop_table("feature_artifacts")
    op.drop_table("source_mapping_versions")
    op.drop_table("source_capabilities")


def _capability_row(
    source_id: str,
    adapter_version: str,
    dataset: str,
    exchanges: list[str],
    eligibility: str,
    priority: int,
    reviewed_at: date,
    registered_at: datetime,
) -> dict[str, object]:
    return {
        "id": uuid.uuid4(),
        "source_id": source_id,
        "adapter_version": adapter_version,
        "dataset": dataset,
        "capabilities": {
            "historical": True,
            "incremental": dataset == "qfq_daily_bars",
            "adjustment": "qfq" if dataset == "qfq_daily_bars" else None,
        },
        "market_coverage": {"exchanges": exchanges},
        "time_semantics": {"as_of": True, "fetched_at": True, "published_at": False},
        "auth_mode": "public",
        "terms_reviewed_at": reviewed_at,
        "formal_eligibility": eligibility,
        "fallback_priority": priority,
        "policy": {"timeouts": True, "finite_retries": True, "rate_limited": True},
        "quality": "missing",
        "last_success_at": None,
        "last_failure_at": None,
        "last_failure_reason": "Awaiting post-migration health refresh",
        "updated_at": registered_at,
    }
