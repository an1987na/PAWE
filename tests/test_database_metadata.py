from pawe_api.db import models as models
from pawe_api.db.base import Base


def test_initial_schema_contains_daily_brief_chain() -> None:
    assert models.DailyBrief.__tablename__ in Base.metadata.tables
    assert models.DailyBriefItem.__tablename__ in Base.metadata.tables
    assert "decision_set_id" in Base.metadata.tables["daily_briefs"].columns
    assert "decision_item_id" in Base.metadata.tables["daily_brief_items"].columns


def test_legacy_data_is_isolated_in_staging_tables() -> None:
    assert models.LegacyMigrationBatch.__tablename__ in Base.metadata.tables
    assert models.LegacyDocumentStaging.__tablename__ in Base.metadata.tables
    assert models.LegacyItemStaging.__tablename__ in Base.metadata.tables
    item_table = Base.metadata.tables["legacy_items_staging"]
    assert "verification_status" in item_table.columns
    assert "conflict_attribution" in item_table.columns
    assert "replay_eligibility" in item_table.columns
    assert "replay_arm" in item_table.columns
    assert not any(
        foreign_key.column.table.name in {"weeks", "decision_sets", "decision_items"}
        for foreign_key in item_table.foreign_keys
    )


def test_candidate_and_approval_audit_tables_are_present() -> None:
    assert models.Candidate.__tablename__ in Base.metadata.tables
    assert models.Approval.__tablename__ in Base.metadata.tables
    assert models.PublicationEvent.__tablename__ in Base.metadata.tables
    assert "source_decision_set_id" in Base.metadata.tables["decision_sets"].columns
    assert "idempotency_key" in Base.metadata.tables["approvals"].columns


def test_authentication_tables_do_not_store_raw_secrets() -> None:
    assert models.User.__tablename__ in Base.metadata.tables
    assert models.UserSession.__tablename__ in Base.metadata.tables
    assert models.AuthEvent.__tablename__ in Base.metadata.tables
    session_columns = Base.metadata.tables["user_sessions"].columns
    assert "token_hash" in session_columns
    assert "csrf_token_hash" in session_columns
    assert "session_token" not in session_columns
    assert "password" not in Base.metadata.tables["users"].columns
    credential_columns = Base.metadata.tables["user_ai_credentials"].columns
    assert "encrypted_api_key" in credential_columns
    assert "api_key" not in credential_columns


def test_personal_watchlist_outputs_are_isolated_from_public_decisions() -> None:
    watchlist = Base.metadata.tables[models.UserWatchlistItem.__tablename__]
    daily = Base.metadata.tables[models.UserWatchlistDailyItem.__tablename__]
    weekly = Base.metadata.tables[models.UserWatchlistWeeklyItem.__tablename__]

    assert {"user_id", "stock_id", "effective_from", "removed_at"} <= set(watchlist.columns.keys())
    assert {"user_id", "watchlist_item_id", "week_id", "trade_date", "payload"} <= set(
        daily.columns.keys()
    )
    assert {"user_id", "watchlist_item_id", "week_id", "payload"} <= set(weekly.columns.keys())
    assert "decision_set_id" not in watchlist.columns
    assert "decision_set_id" not in daily.columns
    assert "decision_set_id" not in weekly.columns


def test_jobs_are_auditable_and_idempotent() -> None:
    assert models.Job.__tablename__ in Base.metadata.tables
    table = Base.metadata.tables["jobs"]
    assert "idempotency_key" in table.columns
    assert "error_code" in table.columns
    assert "details" in table.columns
    assert "checkpoint" in table.columns
    assert "cancel_requested_at" in table.columns


def test_data_baseline_tables_are_versioned_and_snapshot_bound() -> None:
    calendar = Base.metadata.tables[models.TradingCalendar.__tablename__]
    features = Base.metadata.tables[models.WeeklyFeature.__tablename__]
    state_inputs = Base.metadata.tables[models.WeeklyStateInput.__tablename__]

    assert "previous_open_date" in calendar.columns
    assert "quality" in calendar.columns
    assert "snapshot_id" in features.columns
    assert "feature_version" in features.columns
    assert "content_hash" in features.columns
    assert "snapshot_id" in state_inputs.columns
    assert "input_version" in state_inputs.columns

    snapshot_records = Base.metadata.tables[models.DataSnapshotRecord.__tablename__]
    assert "record_key" in snapshot_records.columns
    assert "published_at" in snapshot_records.columns
    assert "adjustment" in snapshot_records.columns
    assert "content_hash" in snapshot_records.columns


def test_market_data_tables_keep_source_and_historical_versions() -> None:
    stocks = Base.metadata.tables[models.Stock.__tablename__]
    classifications = Base.metadata.tables[models.StockClassification.__tablename__]
    daily_bars = Base.metadata.tables[models.DailyBar.__tablename__]

    assert {"source", "quality", "fetched_at", "content_hash", "last_seen_at"} <= set(
        stocks.columns.keys()
    )
    assert {
        "valid_from",
        "valid_to",
        "published_at",
        "evidence_url",
        "source",
        "quality",
    } <= set(classifications.columns.keys())
    primary_indexes = {index.name: index for index in classifications.indexes}
    assert primary_indexes["uq_stock_classification_active_primary"].unique
    daily_unique_columns = {
        column.name
        for constraint in daily_bars.constraints
        if constraint.name == "uq_daily_bar_version"
        for column in constraint.columns
    }
    assert daily_unique_columns == {
        "stock_id",
        "trade_date",
        "adjustment",
        "source",
        "content_hash",
    }


def test_historical_replay_and_weekly_review_are_isolated_and_auditable() -> None:
    replay = Base.metadata.tables[models.HistoricalReplay.__tablename__]
    review = Base.metadata.tables[models.WeeklyReview.__tablename__]
    item = Base.metadata.tables[models.WeeklyReviewItem.__tablename__]

    assert {"decision_cutoff", "actual_run_at", "daily_briefs_payload", "content_hash"} <= set(
        replay.columns.keys()
    )
    assert {"source_type", "decision_set_id", "replay_run_id", "report_markdown"} <= set(
        review.columns.keys()
    )
    assert {"benchmark_excess", "industry_excess", "target_touched"} <= set(item.columns.keys())


def test_experiment_governance_and_feature_artifacts_are_isolated() -> None:
    capability = Base.metadata.tables[models.SourceCapability.__tablename__]
    mapping = Base.metadata.tables[models.SourceMappingVersion.__tablename__]
    artifact = Base.metadata.tables[models.FeatureArtifact.__tablename__]
    proposal = Base.metadata.tables[models.RuleProposal.__tablename__]
    experiment = Base.metadata.tables[models.Experiment.__tablename__]
    run = Base.metadata.tables[models.ExperimentRun.__tablename__]
    fold = Base.metadata.tables[models.ExperimentFold.__tablename__]
    approval = Base.metadata.tables[models.ExperimentApproval.__tablename__]

    assert {"formal_eligibility", "time_semantics", "terms_reviewed_at"} <= set(
        capability.columns.keys()
    )
    assert {"mapping", "validation_result", "approved_by_user_id"} <= set(
        mapping.columns.keys()
    )
    assert {"snapshot_id", "source_hashes", "content_hash", "status", "uri"} <= set(
        artifact.columns.keys()
    )
    assert {"dsl", "validation_result", "rollback_version", "status"} <= set(
        proposal.columns.keys()
    )
    assert "decision_set_id" not in experiment.columns
    assert "decision_set_id" not in run.columns
    assert {"train_start", "selection_start", "validation_start", "metrics"} <= set(
        fold.columns.keys()
    )
    assert {"experiment_version", "action", "created_by_user_id"} <= set(
        approval.columns.keys()
    )
