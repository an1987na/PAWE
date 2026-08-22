import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from pawe_api.db.base import Base


class Stock(Base):
    __tablename__ = "stocks"
    __table_args__ = (UniqueConstraint("code", "exchange", name="uq_stocks_code_exchange"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6))
    exchange: Mapped[str] = mapped_column(String(8))
    board: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(64))
    listing_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16))
    source: Mapped[str | None] = mapped_column(String(32))
    quality: Mapped[str | None] = mapped_column(String(20))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StockClassification(Base):
    __tablename__ = "stock_classifications"
    __table_args__ = (
        UniqueConstraint(
            "stock_id",
            "classification_type",
            "source",
            "valid_from",
            name="uq_stock_classification_version",
        ),
        CheckConstraint(
            "NOT is_primary OR "
            "(classification_type = 'pawe_primary' AND domain IS NOT NULL "
            "AND sector_code IS NOT NULL)",
            name="ck_stock_classification_primary_shape",
        ),
        Index(
            "uq_stock_classification_active_primary",
            "stock_id",
            unique=True,
            postgresql_where=text("is_primary AND valid_to IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    classification_type: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(96))
    domain: Mapped[str | None] = mapped_column(String(20))
    sector_code: Mapped[str | None] = mapped_column(String(64))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(32))
    quality: Mapped[str] = mapped_column(String(20))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    published_at: Mapped[date | None] = mapped_column(Date)
    evidence_url: Mapped[str | None] = mapped_column(String(512))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64))


class DailyBar(Base):
    __tablename__ = "daily_bars"
    __table_args__ = (
        UniqueConstraint(
            "stock_id",
            "trade_date",
            "adjustment",
            "source",
            "content_hash",
            name="uq_daily_bar_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    high: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    low: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    close: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    volume: Mapped[Decimal] = mapped_column(Numeric(24, 4))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    adjustment: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(32))
    quality: Mapped[str] = mapped_column(String(20))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64))


class DataSnapshot(Base):
    __tablename__ = "data_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quality: Mapped[str] = mapped_column(String(20))
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DataSnapshotRecord(Base):
    __tablename__ = "data_snapshot_records"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "record_key",
            "source",
            name="uq_data_snapshot_record_source",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_snapshots.id"), index=True
    )
    record_key: Mapped[str] = mapped_column(String(96))
    source: Mapped[str] = mapped_column(String(32))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[str | None] = mapped_column(String(40))
    adjustment: Mapped[str | None] = mapped_column(String(16))
    quality: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))


class TradingCalendar(Base):
    __tablename__ = "trading_calendar"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_open: Mapped[bool] = mapped_column(Boolean)
    previous_open_date: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(32))
    quality: Mapped[str] = mapped_column(String(20))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64))


class WeeklyFeature(Base):
    __tablename__ = "weekly_features"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "stock_id",
            "feature_version",
            name="uq_weekly_feature_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_snapshots.id"), index=True
    )
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    feature_version: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FeatureArtifact(Base):
    __tablename__ = "feature_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "partition_key",
            "schema_version",
            "feature_version",
            "code_version",
            name="uq_feature_artifact_build",
        ),
        CheckConstraint(
            "status IN ('building', 'published', 'failed', 'cancelled')",
            name="ck_feature_artifact_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_snapshots.id"), index=True
    )
    partition_key: Mapped[str] = mapped_column(String(96))
    schema_version: Mapped[str] = mapped_column(String(32))
    feature_version: Mapped[str] = mapped_column(String(32))
    code_version: Mapped[str] = mapped_column(String(64))
    decision_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_hashes: Mapped[list[str]] = mapped_column(JSONB)
    row_count: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    quality: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), index=True)
    uri: Mapped[str | None] = mapped_column(Text)
    build_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WeeklyStateInput(Base):
    __tablename__ = "weekly_state_inputs"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "input_version", name="uq_weekly_state_input_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_snapshots.id"), index=True
    )
    input_version: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Week(Base):
    __tablename__ = "weeks"

    week_id: Mapped[date] = mapped_column(Date, primary_key=True)
    status: Mapped[str] = mapped_column(String(32))
    market_state: Mapped[str] = mapped_column(String(32))
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_snapshots.id")
    )
    rule_version: Mapped[str] = mapped_column(String(64))


class DecisionSet(Base):
    __tablename__ = "decision_sets"
    __table_args__ = (
        UniqueConstraint("week_id", "type", "version", name="uq_decision_set_version"),
        Index("ix_decision_sets_week_type_active", "week_id", "type", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_decision_set_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decision_sets.id")
    )
    week_id: Mapped[date] = mapped_column(ForeignKey("weeks.week_id"))
    type: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    fingerprint: Mapped[str] = mapped_column(String(64))
    shortage: Mapped[bool] = mapped_column(Boolean)
    shortage_reason: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DecisionItem(Base):
    __tablename__ = "decision_items"
    __table_args__ = (
        UniqueConstraint("decision_set_id", "rank", name="uq_decision_item_rank"),
        UniqueConstraint("decision_set_id", "stock_id", name="uq_decision_item_stock"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    decision_set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decision_sets.id"))
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"))
    rank: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(24))
    target_return: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    confidence: Mapped[str] = mapped_column(String(12))
    summary: Mapped[str] = mapped_column(String(160))
    primary_risk: Mapped[str] = mapped_column(String(120))


class Candidate(Base):
    __tablename__ = "candidates"
    __table_args__ = (UniqueConstraint("week_id", "stock_id", name="uq_candidate_week_stock"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    week_id: Mapped[date] = mapped_column(ForeignKey("weeks.week_id"), index=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_snapshots.id"))
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"))
    rule_score: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    rank: Mapped[int | None] = mapped_column(Integer)
    bucket: Mapped[str] = mapped_column(String(32))
    exclusion_reasons: Mapped[list[str]] = mapped_column(JSON)
    score_breakdown: Mapped[dict[str, float]] = mapped_column(JSON)


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("week_id", "idempotency_key", name="uq_approval_idempotency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    week_id: Mapped[date] = mapped_column(ForeignKey("weeks.week_id"), index=True)
    source_decision_set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decision_sets.id"))
    approved_decision_set_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decision_sets.id")
    )
    decision_version: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(24))
    selected_codes: Mapped[list[str]] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PublicationEvent(Base):
    __tablename__ = "publication_events"
    __table_args__ = (
        UniqueConstraint("week_id", "idempotency_key", name="uq_publication_idempotency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    week_id: Mapped[date] = mapped_column(ForeignKey("weeks.week_id"), index=True)
    decision_set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decision_sets.id"))
    decision_version: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin', 'viewer')", name="ck_users_role"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserAICredential(Base):
    __tablename__ = "user_ai_credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), unique=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(24))
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    key_hint: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(96))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthEvent(Base):
    __tablename__ = "auth_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    username: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, str]] = mapped_column(JSONB)


class UserWatchlistItem(Base):
    __tablename__ = "user_watchlist_items"
    __table_args__ = (
        Index("ix_user_watchlist_user_active", "user_id", "removed_at"),
        Index(
            "uq_user_watchlist_active_stock",
            "user_id",
            "stock_id",
            unique=True,
            postgresql_where=text("removed_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_from: Mapped[date] = mapped_column(Date)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserWatchlistDailyItem(Base):
    __tablename__ = "user_watchlist_daily_items"
    __table_args__ = (
        UniqueConstraint("user_id", "trade_date", "stock_id", name="uq_watch_daily_item"),
        Index("ix_watch_daily_user_week", "user_id", "week_id", "trade_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    watchlist_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_watchlist_items.id")
    )
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"))
    week_id: Mapped[date] = mapped_column(Date)
    trade_date: Mapped[date] = mapped_column(Date)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quality: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)


class UserWatchlistWeeklyItem(Base):
    __tablename__ = "user_watchlist_weekly_items"
    __table_args__ = (
        UniqueConstraint("user_id", "week_id", "stock_id", name="uq_watch_weekly_item"),
        Index("ix_watch_weekly_user_week", "user_id", "week_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    watchlist_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_watchlist_items.id")
    )
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"))
    week_id: Mapped[date] = mapped_column(Date)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quality: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)


class DailyBrief(Base):
    __tablename__ = "daily_briefs"
    __table_args__ = (
        UniqueConstraint(
            "week_id",
            "trade_date",
            "decision_set_id",
            "version",
            name="uq_daily_brief_version",
        ),
        Index(
            "ix_daily_briefs_week_date_active",
            "week_id",
            "trade_date",
            "is_active",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    week_id: Mapped[date] = mapped_column(ForeignKey("weeks.week_id"))
    trade_date: Mapped[date] = mapped_column(Date)
    decision_set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decision_sets.id"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quality: Mapped[str] = mapped_column(String(20))
    ai_degraded: Mapped[bool] = mapped_column(Boolean)
    summary: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class DailyBriefItem(Base):
    __tablename__ = "daily_brief_items"
    __table_args__ = (
        UniqueConstraint("daily_brief_id", "decision_item_id", name="uq_daily_brief_item"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    daily_brief_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("daily_briefs.id"))
    decision_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("decision_items.id"))
    daily_return: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    week_to_date_return: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    week_high_return: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    drawdown_from_week_high: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    distance_to_target: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    volume_activity: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    risk_status: Mapped[str] = mapped_column(String(24))
    summary: Mapped[str] = mapped_column(String(320))
    evidence_ids: Mapped[list[str]] = mapped_column(JSON)


class HistoricalReplay(Base):
    __tablename__ = "historical_replays"
    __table_args__ = (
        UniqueConstraint(
            "week_id",
            "rule_version",
            name="uq_historical_replay_week_rule",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    week_id: Mapped[date] = mapped_column(Date, index=True)
    rule_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    decision_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    simulated_selection_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    simulated_review_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actual_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quality: Mapped[str] = mapped_column(String(20))
    selected_codes: Mapped[list[str]] = mapped_column(JSON)
    decision_payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    daily_briefs_payload: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    warnings: Mapped[list[str]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReplayRun(Base):
    """Isolated manual replay run; it must never point at formal output rows."""

    __tablename__ = "replay_runs"
    __table_args__ = (
        Index("ix_replay_runs_week_stage", "week_id", "requested_stage", "status"),
        UniqueConstraint(
            "week_id",
            "requested_stage",
            "trade_date",
            "idempotency_key",
            name="uq_replay_run_idempotency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    week_id: Mapped[date] = mapped_column(Date, index=True)
    requested_stage: Mapped[str] = mapped_column(String(24))
    trade_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(24), index=True)
    rule_version: Mapped[str] = mapped_column(String(64))
    effective_rule_version: Mapped[str] = mapped_column(String(64))
    information_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    simulated_selection_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    simulated_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    simulated_trade_date: Mapped[date | None] = mapped_column(Date)
    actual_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    warnings: Mapped[list[str]] = mapped_column(JSON)
    details: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReplayStageRun(Base):
    __tablename__ = "replay_stage_runs"
    __table_args__ = (
        UniqueConstraint(
            "replay_run_id",
            "stage",
            "trade_date",
            name="uq_replay_stage_run_target",
        ),
        Index("ix_replay_stage_runs_status", "status", "stage"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    replay_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("replay_runs.id"), index=True
    )
    stage: Mapped[str] = mapped_column(String(24))
    trade_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(24), index=True)
    information_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actual_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    warnings: Mapped[list[str]] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(240))
    details: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReplayDecisionSet(Base):
    __tablename__ = "replay_decision_sets"
    __table_args__ = (
        UniqueConstraint(
            "replay_stage_run_id",
            "version",
            name="uq_replay_decision_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    replay_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("replay_runs.id"), index=True
    )
    replay_stage_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("replay_stage_runs.id"), index=True
    )
    week_id: Mapped[date] = mapped_column(Date, index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    fingerprint: Mapped[str] = mapped_column(String(64))
    rule_version: Mapped[str] = mapped_column(String(64))
    information_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReplayDecisionItem(Base):
    __tablename__ = "replay_decision_items"
    __table_args__ = (
        UniqueConstraint("replay_decision_set_id", "rank", name="uq_replay_decision_item_rank"),
        UniqueConstraint(
            "replay_decision_set_id", "stock_id", name="uq_replay_decision_item_stock"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    replay_decision_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("replay_decision_sets.id"), index=True
    )
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"))
    rank: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(24))
    target_return: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    confidence: Mapped[str] = mapped_column(String(12))
    summary: Mapped[str] = mapped_column(String(160))
    primary_risk: Mapped[str] = mapped_column(String(120))


class ReplayDailyBrief(Base):
    __tablename__ = "replay_daily_briefs"
    __table_args__ = (
        UniqueConstraint(
            "replay_stage_run_id",
            "trade_date",
            "version",
            name="uq_replay_daily_brief_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    replay_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("replay_runs.id"), index=True
    )
    replay_stage_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("replay_stage_runs.id"), index=True
    )
    replay_decision_set_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("replay_decision_sets.id")
    )
    week_id: Mapped[date] = mapped_column(Date, index=True)
    trade_date: Mapped[date] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quality: Mapped[str] = mapped_column(String(20))
    ai_degraded: Mapped[bool] = mapped_column(Boolean)
    summary: Mapped[str] = mapped_column(Text)


class ReplayDailyBriefItem(Base):
    __tablename__ = "replay_daily_brief_items"
    __table_args__ = (
        UniqueConstraint(
            "replay_daily_brief_id",
            "stock_id",
            name="uq_replay_daily_brief_item_stock",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    replay_daily_brief_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("replay_daily_briefs.id"), index=True
    )
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"))
    daily_return: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    week_to_date_return: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    week_high_return: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    drawdown_from_week_high: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    distance_to_target: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    volume_activity: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    risk_status: Mapped[str] = mapped_column(String(24))
    summary: Mapped[str] = mapped_column(String(320))
    evidence_ids: Mapped[list[str]] = mapped_column(JSON)


class ReplayWeeklyReview(Base):
    __tablename__ = "replay_weekly_reviews"
    __table_args__ = (
        UniqueConstraint("replay_stage_run_id", name="uq_replay_weekly_review_stage"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    replay_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("replay_runs.id"), index=True
    )
    replay_stage_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("replay_stage_runs.id"), index=True
    )
    replay_decision_set_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("replay_decision_sets.id")
    )
    week_id: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(24))
    entry_trade_date: Mapped[date] = mapped_column(Date)
    final_trade_date: Mapped[date] = mapped_column(Date)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quality: Mapped[str] = mapped_column(String(20))
    aggregate: Mapped[dict[str, object]] = mapped_column(JSONB)
    summary: Mapped[str] = mapped_column(Text)
    warnings: Mapped[list[str]] = mapped_column(JSON)


class ReplayWeeklyReviewItem(Base):
    __tablename__ = "replay_weekly_review_items"
    __table_args__ = (
        UniqueConstraint(
            "replay_weekly_review_id",
            "stock_id",
            name="uq_replay_weekly_review_item_stock",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    replay_weekly_review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("replay_weekly_reviews.id"), index=True
    )
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"))
    rank: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    week_high_return: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    week_close_return: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    max_drawdown_from_entry: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    max_peak_to_trough_drawdown: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    target_touched: Mapped[bool] = mapped_column(Boolean)
    target_touch_date: Mapped[date | None] = mapped_column(Date)
    drawdown_before_touch: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    accessible_at_entry: Mapped[bool] = mapped_column(Boolean)
    benchmark_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    benchmark_excess: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    industry_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    industry_excess: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))


class WeeklyReview(Base):
    __tablename__ = "weekly_reviews"
    __table_args__ = (
        UniqueConstraint(
            "week_id",
            "source_type",
            "source_version",
            "rule_version",
            name="uq_weekly_review_source",
        ),
        Index("ix_weekly_reviews_week_active", "week_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    week_id: Mapped[date] = mapped_column(Date, index=True)
    source_type: Mapped[str] = mapped_column(String(24))
    source_version: Mapped[int] = mapped_column(Integer)
    rule_version: Mapped[str] = mapped_column(String(64))
    decision_set_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decision_sets.id")
    )
    replay_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("historical_replays.id")
    )
    status: Mapped[str] = mapped_column(String(24))
    entry_trade_date: Mapped[date] = mapped_column(Date)
    final_trade_date: Mapped[date] = mapped_column(Date)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quality: Mapped[str] = mapped_column(String(20))
    aggregate: Mapped[dict[str, object]] = mapped_column(JSONB)
    summary: Mapped[str] = mapped_column(Text)
    report_markdown: Mapped[str] = mapped_column(Text)
    warnings: Mapped[list[str]] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class WeeklyReviewItem(Base):
    __tablename__ = "weekly_review_items"
    __table_args__ = (
        UniqueConstraint("weekly_review_id", "stock_id", name="uq_weekly_review_item"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    weekly_review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("weekly_reviews.id"), index=True
    )
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"))
    rank: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    week_high_return: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    week_close_return: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    max_drawdown_from_entry: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    max_peak_to_trough_drawdown: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    target_touched: Mapped[bool] = mapped_column(Boolean)
    target_touch_date: Mapped[date | None] = mapped_column(Date)
    drawdown_before_touch: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    accessible_at_entry: Mapped[bool] = mapped_column(Boolean)
    benchmark_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    benchmark_excess: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    industry_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    industry_excess: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))


class SourceCapability(Base):
    __tablename__ = "source_capabilities"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "adapter_version",
            "dataset",
            name="uq_source_capability_version",
        ),
        CheckConstraint(
            "formal_eligibility IN ('formal', 'research_only', 'disabled')",
            name="ck_source_capability_eligibility",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(32), index=True)
    adapter_version: Mapped[str] = mapped_column(String(32))
    dataset: Mapped[str] = mapped_column(String(48), index=True)
    capabilities: Mapped[dict[str, object]] = mapped_column(JSONB)
    market_coverage: Mapped[dict[str, object]] = mapped_column(JSONB)
    time_semantics: Mapped[dict[str, object]] = mapped_column(JSONB)
    auth_mode: Mapped[str] = mapped_column(String(24))
    terms_reviewed_at: Mapped[date | None] = mapped_column(Date)
    formal_eligibility: Mapped[str] = mapped_column(String(20))
    fallback_priority: Mapped[int] = mapped_column(Integer)
    policy: Mapped[dict[str, object]] = mapped_column(JSONB)
    quality: Mapped[str] = mapped_column(String(20))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_reason: Mapped[str | None] = mapped_column(String(240))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SourceMappingVersion(Base):
    __tablename__ = "source_mapping_versions"
    __table_args__ = (
        UniqueConstraint("source_id", "dataset", "version", name="uq_source_mapping_version"),
        CheckConstraint(
            "status IN ('draft', 'validated', 'approved', 'rejected', 'superseded')",
            name="ck_source_mapping_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(32), index=True)
    dataset: Mapped[str] = mapped_column(String(48), index=True)
    version: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str] = mapped_column(String(32))
    mapping: Mapped[dict[str, object]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20))
    validation_result: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RuleProposal(Base):
    __tablename__ = "rule_proposals"
    __table_args__ = (
        UniqueConstraint("proposal_key", name="uq_rule_proposal_key"),
        CheckConstraint(
            "status IN ('proposed', 'schema_validated', 'invalid')",
            name="ck_rule_proposal_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    proposal_key: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str] = mapped_column(String(16))
    base_rule_version: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(24))
    hypothesis: Mapped[str] = mapped_column(Text)
    dsl: Mapped[dict[str, object]] = mapped_column(JSONB)
    objectives: Mapped[list[str]] = mapped_column(JSONB)
    required_features: Mapped[list[str]] = mapped_column(JSONB)
    invalidation_conditions: Mapped[list[str]] = mapped_column(JSONB)
    rollback_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), index=True)
    validation_result: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AIInvocation(Base):
    __tablename__ = "ai_invocations"
    __table_args__ = (
        Index("ix_ai_invocations_capability_created", "capability", "created_at"),
        Index("ix_ai_invocations_subject", "subject_type", "subject_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    capability: Mapped[str] = mapped_column(String(32), index=True)
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(96))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(96))
    prompt_hash: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(32))
    policy_version: Mapped[str] = mapped_column(String(32))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    context: Mapped[dict[str, object]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24), index=True)
    structured_input: Mapped[dict[str, object]] = mapped_column(JSONB)
    structured_output: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    usage: Mapped[dict[str, object]] = mapped_column(JSONB)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(240))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIAudit(Base):
    __tablename__ = "ai_audits"
    __table_args__ = (Index("ix_ai_audits_capability_created", "capability", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    invocation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_invocations.id"), index=True)
    capability: Mapped[str] = mapped_column(String(32))
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(96))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    output_hash: Mapped[str | None] = mapped_column(String(64))
    validation: Mapped[dict[str, object]] = mapped_column(JSONB)
    warnings: Mapped[list[str]] = mapped_column(JSON)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AICandidateAnalysis(Base):
    __tablename__ = "ai_candidate_analyses"
    __table_args__ = (
        UniqueConstraint("invocation_id", "stock_id", name="uq_ai_candidate_analysis_stock"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    invocation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_invocations.id"), index=True)
    replay_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("replay_runs.id"), index=True
    )
    week_id: Mapped[date] = mapped_column(Date, index=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    adjustment: Mapped[int] = mapped_column(Integer)
    accepted: Mapped[bool] = mapped_column(Boolean)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ErrorAttribution(Base):
    __tablename__ = "error_attributions"
    __table_args__ = (Index("ix_error_attributions_week_status", "week_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    week_id: Mapped[date] = mapped_column(Date, index=True)
    review_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("weekly_reviews.id"), index=True)
    invocation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_invocations.id"), index=True
    )
    taxonomy: Mapped[str] = mapped_column(String(48))
    confidence: Mapped[str] = mapped_column(String(16))
    facts: Mapped[dict[str, object]] = mapped_column(JSONB)
    proposed_hypothesis: Mapped[str] = mapped_column(Text)
    counterfactual_allowed: Mapped[bool] = mapped_column(Boolean)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), index=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AttributionResolution(Base):
    __tablename__ = "attribution_resolutions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    attribution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("error_attributions.id"), index=True
    )
    action: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(500))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Experiment(Base):
    __tablename__ = "experiments"
    __table_args__ = (UniqueConstraint("rule_proposal_id", name="uq_experiment_rule_proposal"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    rule_proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rule_proposals.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    baseline_rule_version: Mapped[str] = mapped_column(String(64))
    candidate_rule_version: Mapped[str] = mapped_column(String(64))
    rollback_version: Mapped[str] = mapped_column(String(64))
    activated_rule_version: Mapped[str | None] = mapped_column(String(64))
    status_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExperimentRun(Base):
    __tablename__ = "experiment_runs"
    __table_args__ = (
        UniqueConstraint("experiment_id", "run_type", "attempt", name="uq_experiment_run"),
        CheckConstraint("run_type IN ('replay', 'shadow')", name="ck_experiment_run_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), index=True
    )
    run_type: Mapped[str] = mapped_column(String(16))
    attempt: Mapped[int] = mapped_column(Integer)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), index=True)
    metrics: Mapped[dict[str, object]] = mapped_column(JSONB)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExperimentFold(Base):
    __tablename__ = "experiment_folds"
    __table_args__ = (UniqueConstraint("run_id", "fold_index", name="uq_experiment_fold"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiment_runs.id"), index=True
    )
    fold_index: Mapped[int] = mapped_column(Integer)
    train_start: Mapped[date] = mapped_column(Date)
    train_end: Mapped[date] = mapped_column(Date)
    selection_start: Mapped[date] = mapped_column(Date)
    selection_end: Mapped[date] = mapped_column(Date)
    validation_start: Mapped[date] = mapped_column(Date)
    validation_end: Mapped[date] = mapped_column(Date)
    snapshot_ids: Mapped[list[str]] = mapped_column(JSONB)
    sample_count: Mapped[int] = mapped_column(Integer)
    capacity_distribution: Mapped[dict[str, int]] = mapped_column(JSONB)
    metrics: Mapped[dict[str, object]] = mapped_column(JSONB)
    integrity_status: Mapped[str] = mapped_column(String(24))


class ExperimentApproval(Base):
    __tablename__ = "experiment_approvals"
    __table_args__ = (
        UniqueConstraint("experiment_id", "experiment_version", name="uq_experiment_approval"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id"), index=True
    )
    experiment_version: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("job_type", "week_id", "idempotency_key", name="uq_job_idempotency"),
        Index(
            "uq_jobs_active_weekly_selection",
            "week_id",
            unique=True,
            postgresql_where=text(
                "job_type = 'weekly_selection' AND status IN ('queued', 'running')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(32), index=True)
    mode: Mapped[str] = mapped_column(String(16), server_default=text("'formal'"), index=True)
    replay_stage: Mapped[str | None] = mapped_column(String(24), index=True)
    trade_date: Mapped[date | None] = mapped_column(Date)
    replay_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("replay_runs.id"), index=True
    )
    week_id: Mapped[date | None] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    error_code: Mapped[str | None] = mapped_column(String(48))
    error_message: Mapped[str | None] = mapped_column(String(240))
    checkpoint: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LegacyMigrationBatch(Base):
    __tablename__ = "legacy_migration_batches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_label: Mapped[str] = mapped_column(String(64))
    manifest_hash: Mapped[str] = mapped_column(String(64), unique=True)
    source_file_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LegacyDocumentStaging(Base):
    __tablename__ = "legacy_documents_staging"
    __table_args__ = (UniqueConstraint("batch_id", "source_ref", name="uq_legacy_document_source"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("legacy_migration_batches.id"), index=True
    )
    source_ref: Mapped[str] = mapped_column(Text)
    source_hash: Mapped[str] = mapped_column(String(64))
    document_type: Mapped[str] = mapped_column(String(32))
    document_date: Mapped[date | None] = mapped_column(Date)
    rule_version: Mapped[str | None] = mapped_column(String(32))
    linked_source_ref: Mapped[str | None] = mapped_column(Text)
    parse_quality: Mapped[str] = mapped_column(String(20))
    verification_status: Mapped[str] = mapped_column(String(24))
    warnings: Mapped[list[str]] = mapped_column(JSON)


class LegacyItemStaging(Base):
    __tablename__ = "legacy_items_staging"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "bucket",
            "stock_code",
            name="uq_legacy_item_document_bucket_stock",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("legacy_documents_staging.id"), index=True
    )
    bucket: Mapped[str] = mapped_column(String(16))
    stock_code: Mapped[str] = mapped_column(String(6))
    stock_name: Mapped[str] = mapped_column(String(64))
    direction: Mapped[str | None] = mapped_column(String(120))
    rank: Mapped[int | None] = mapped_column(Integer)
    baseline_price: Mapped[Decimal | None] = mapped_column(Numeric(16, 6))
    target_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    week_high_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    close_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    max_drawdown: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    verification_status: Mapped[str] = mapped_column(String(24))
    verification_source: Mapped[str | None] = mapped_column(String(32))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    legacy_recalculated: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    v9_recalculated: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    verification_warnings: Mapped[list[str] | None] = mapped_column(JSONB)
    conflict_attribution: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    replay_eligibility: Mapped[str | None] = mapped_column(String(32), index=True)
    replay_arm: Mapped[str | None] = mapped_column(String(24))
