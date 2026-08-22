from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class DataQuality(StrEnum):
    VERIFIED = "verified"
    SINGLE_SOURCE = "single_source"
    DEGRADED = "degraded"
    CONFLICTED = "conflicted"
    MISSING = "missing"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class MarketState(StrEnum):
    NORMAL = "NORMAL"
    ANCHOR_DISTORTED = "ANCHOR_DISTORTED"
    SYSTEMIC_RETREAT = "SYSTEMIC_RETREAT"
    BREADTH_RECOVERY = "BREADTH_RECOVERY"
    RECOVERY_CONFIRMED = "RECOVERY_CONFIRMED"
    RECOVERY_FAILED = "RECOVERY_FAILED"


class WeeklyStatus(StrEnum):
    CREATED = "created"
    SNAPSHOT_READY = "snapshot_ready"
    RULE_READY = "rule_ready"
    AI_READY = "ai_ready"
    AI_DEGRADED = "ai_degraded"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PUBLISHED = "published"
    REVIEWED = "reviewed"
    FAILED = "failed"


class DailyRiskStatus(StrEnum):
    ON_TRACK = "on_track"
    WATCH = "watch"
    RISK_TRIGGERED = "risk_triggered"
    DATA_DEGRADED = "data_degraded"


class RuleCondition(BaseModel):
    all: list["RuleCondition"] | None = Field(default=None, min_length=1, max_length=20)
    any: list["RuleCondition"] | None = Field(default=None, min_length=1, max_length=20)
    negate: "RuleCondition | None" = Field(default=None, alias="not")
    feature: str | None = Field(default=None, min_length=1, max_length=64)
    op: Literal["eq", "in", "gt", "gte", "lt", "lte", "between"] | None = None
    value: Any = None

    @model_validator(mode="after")
    def validate_shape(self) -> "RuleCondition":
        groups = [self.all is not None, self.any is not None, self.negate is not None]
        leaf = self.feature is not None or self.op is not None or self.value is not None
        if sum(groups) + int(leaf) != 1:
            raise ValueError("condition must contain exactly one logical group or comparison")
        if leaf and (self.feature is None or self.op is None or self.value is None):
            raise ValueError("comparison requires feature, op, and value")
        return self


class RuleParameterChange(BaseModel):
    parameter: str = Field(min_length=1, max_length=64)
    op: Literal["set"] = "set"
    value: float


class RuleProposalRequest(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    proposal_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{7,63}$")
    base_rule_version: str = Field(min_length=1, max_length=64)
    scope: Literal["ranking", "scoring", "market_state"]
    hypothesis: str = Field(min_length=20, max_length=1000)
    conditions: RuleCondition
    changes: list[RuleParameterChange] = Field(min_length=1, max_length=20)
    objective: list[
        Literal[
            "touch_10_rate",
            "pre_touch_drawdown",
            "close_retention",
            "benchmark_excess",
            "industry_excess",
            "anchor_contribution_share",
            "probability_calibration",
        ]
    ] = Field(min_length=1, max_length=7)
    required_features: list[str] = Field(min_length=1, max_length=50)
    expected_effect: str = Field(min_length=10, max_length=1000)
    invalidation_conditions: list[str] = Field(min_length=1, max_length=20)
    rollback_version: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_unique_lists(self) -> "RuleProposalRequest":
        if len(self.objective) != len(set(self.objective)):
            raise ValueError("objective entries must be unique")
        if len(self.required_features) != len(set(self.required_features)):
            raise ValueError("required_features entries must be unique")
        parameters = [change.parameter for change in self.changes]
        if len(parameters) != len(set(parameters)):
            raise ValueError("a proposal cannot change the same parameter twice")
        return self


class RuleProposalResponse(BaseModel):
    id: str
    proposal_id: str
    version: int = Field(ge=1)
    status: Literal["proposed", "schema_validated", "invalid"]
    validation_result: dict[str, object]
    created_at: datetime
    updated_at: datetime


class RuleProposalValidationRequest(BaseModel):
    expected_version: int = Field(ge=1)


class ExperimentActionRequest(BaseModel):
    expected_version: int = Field(ge=1)
    input_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=8, max_length=1000)


class ExperimentApprovalRequest(BaseModel):
    expected_version: int = Field(ge=1)
    action: Literal["approve", "reject"]
    reason: str = Field(min_length=8, max_length=1000)


class ExperimentResponse(BaseModel):
    id: str
    proposal_id: str
    version: int = Field(ge=1)
    status: str
    baseline_rule_version: str
    candidate_rule_version: str
    rollback_version: str
    activated_rule_version: str | None = None
    status_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class ExperimentRunResponse(BaseModel):
    id: str
    experiment_id: str
    run_type: Literal["replay", "shadow"]
    attempt: int = Field(ge=1)
    input_fingerprint: str
    status: str
    metrics: dict[str, object]
    failure_reason: str | None = None
    created_at: datetime


class ExperimentRunStartRequest(BaseModel):
    expected_version: int = Field(ge=1)


class ExperimentFoldResult(BaseModel):
    fold_index: int = Field(ge=1)
    train_start: date
    train_end: date
    selection_start: date
    selection_end: date
    validation_start: date
    validation_end: date
    snapshot_ids: list[str] = Field(min_length=1)
    sample_count: int = Field(ge=0)
    capacity_distribution: dict[str, int]
    metrics: dict[str, object]
    integrity_status: Literal["complete", "incomplete", "conflicted"]

    @model_validator(mode="after")
    def validate_windows(self) -> "ExperimentFoldResult":
        if not (
            self.train_start
            <= self.train_end
            < self.selection_start
            <= self.selection_end
            < self.validation_start
            <= self.validation_end
        ):
            raise ValueError("walk-forward windows must be ordered and non-overlapping")
        if any(key not in {"0", "1", "2", "3", "4", "5"} for key in self.capacity_distribution):
            raise ValueError("capacity_distribution only accepts capacities zero through five")
        if any(count < 0 for count in self.capacity_distribution.values()):
            raise ValueError("capacity_distribution counts cannot be negative")
        return self


class ExperimentRunCompleteRequest(BaseModel):
    expected_version: int = Field(ge=1)
    outcome: Literal["passed", "rejected", "failed"]
    metrics: dict[str, object] = Field(default_factory=dict)
    failure_reason: str | None = Field(default=None, max_length=1000)
    folds: list[ExperimentFoldResult] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_outcome(self) -> "ExperimentRunCompleteRequest":
        if self.outcome == "failed" and not self.failure_reason:
            raise ValueError("failure_reason is required for a failed run")
        if self.outcome != "failed" and self.failure_reason:
            raise ValueError("failure_reason is only valid for a failed run")
        return self


class ExperimentRunUpdateResponse(BaseModel):
    run: ExperimentRunResponse
    experiment: ExperimentResponse


class SourceCapabilityResponse(BaseModel):
    source_id: str
    adapter_version: str
    dataset: str
    capabilities: dict[str, object]
    market_coverage: dict[str, object]
    time_semantics: dict[str, object]
    auth_mode: str
    terms_reviewed_at: date | None
    formal_eligibility: Literal["formal", "research_only", "disabled"]
    fallback_priority: int
    quality: DataQuality
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_failure_reason: str | None
    updated_at: datetime


class FeatureArtifactResponse(BaseModel):
    id: str
    snapshot_id: str
    partition_key: str
    schema_version: str
    feature_version: str
    code_version: str
    decision_cutoff: datetime
    row_count: int = Field(ge=0)
    content_hash: str
    quality: DataQuality
    status: Literal["building", "published", "failed", "cancelled"]
    uri: str | None
    created_at: datetime
    published_at: datetime | None


class ApprovalAction(StrEnum):
    ACCEPT_RULE = "accept_rule"
    ACCEPT_AI = "accept_ai"
    MODIFY = "modify"
    REJECT = "reject"


class ApprovalRequest(BaseModel):
    action: ApprovalAction
    source_type: Literal["rule", "ai"]
    selected_codes: list[str] = Field(max_length=5)
    reason: str = Field(min_length=1, max_length=500)
    decision_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=64)

    @model_validator(mode="after")
    def validate_selection(self) -> "ApprovalRequest":
        if len(self.selected_codes) != len(set(self.selected_codes)):
            raise ValueError("selected_codes must be unique")
        if any(not code.isdigit() or len(code) != 6 for code in self.selected_codes):
            raise ValueError("selected_codes must contain six-digit stock codes")
        if self.action is ApprovalAction.REJECT and self.selected_codes:
            raise ValueError("reject action cannot contain selected codes")
        if self.action is not ApprovalAction.REJECT and not self.selected_codes:
            raise ValueError("approval action requires selected codes")
        return self


class PublishRequest(BaseModel):
    decision_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=64)


class DecisionVersionItem(BaseModel):
    stock_code: str = Field(pattern=r"^\d{6}$")
    stock_name: str
    rank: int = Field(ge=1, le=5)


class DecisionVersionResponse(BaseModel):
    week_id: date
    decision_type: Literal["rule", "ai", "published"]
    version: int = Field(ge=1)
    status: str
    fingerprint: str
    source_type: str | None = None
    source_version: int | None = None
    items: list[DecisionVersionItem]


class ApprovalResponse(BaseModel):
    approval_id: str
    action: ApprovalAction
    approved_decision: DecisionVersionResponse | None


class DecisionItem(BaseModel):
    stock_code: str = Field(pattern=r"^\d{6}$")
    stock_name: str = Field(min_length=1, max_length=32)
    rank: int = Field(ge=1, le=5)
    target_return: float = Field(default=0.10, ge=0.10, le=0.10)
    confidence: Confidence
    summary: str = Field(max_length=80)
    primary_risk: str = Field(max_length=60)
    primary_sector: str | None = Field(default=None, max_length=64)
    rule_score: float | None = None
    selection_reasons: list[str] = Field(default_factory=list, max_length=8)
    score_breakdown: dict[str, float] = Field(default_factory=dict)


class WeekSummary(BaseModel):
    week_id: date
    status: WeeklyStatus
    market_state: MarketState
    decision_version: int = Field(ge=1)
    confidence: Confidence
    shortage: bool
    shortage_reason: str | None = Field(default=None, max_length=200)
    items: list[DecisionItem] = Field(max_length=5)

    @model_validator(mode="after")
    def validate_capacity(self) -> "WeekSummary":
        codes = [item.stock_code for item in self.items]
        ranks = [item.rank for item in self.items]
        if len(codes) != len(set(codes)):
            raise ValueError("decision items must have unique stock codes")
        if sorted(ranks) != list(range(1, len(ranks) + 1)):
            raise ValueError("decision ranks must be contiguous from one")
        if self.shortage != (len(self.items) < 5):
            raise ValueError("shortage must match the actual published capacity")
        if self.shortage and not self.shortage_reason:
            raise ValueError("shortage_reason is required when fewer than five items are published")
        return self


class DailyBriefItem(BaseModel):
    stock_code: str = Field(pattern=r"^\d{6}$")
    stock_name: str = Field(min_length=1, max_length=32)
    daily_return: float
    week_to_date_return: float
    week_high_return: float
    drawdown_from_week_high: float
    distance_to_target: float
    volume_activity: float | None = Field(default=None, ge=0)
    risk_status: DailyRiskStatus
    summary: str = Field(max_length=160)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class DailyBrief(BaseModel):
    week_id: date
    trade_date: date
    decision_version: int = Field(ge=1)
    as_of: datetime
    fetched_at: datetime
    quality: DataQuality
    ai_degraded: bool
    items: list[DailyBriefItem] = Field(max_length=5)

    @model_validator(mode="after")
    def validate_unique_items(self) -> "DailyBrief":
        codes = [item.stock_code for item in self.items]
        if len(codes) != len(set(codes)):
            raise ValueError("daily brief items must have unique stock codes")
        if self.as_of > self.fetched_at:
            raise ValueError("as_of cannot be later than fetched_at")
        return self


class WatchlistAddRequest(BaseModel):
    stock_code: str = Field(pattern=r"^\d{6}$")


class WatchlistItemResponse(BaseModel):
    id: str
    stock_code: str = Field(pattern=r"^\d{6}$")
    stock_name: str
    exchange: str
    board: str
    added_at: datetime
    effective_from: date


class StockSearchResult(BaseModel):
    stock_code: str = Field(pattern=r"^\d{6}$")
    stock_name: str
    exchange: str
    board: str
    already_followed: bool = False


class WatchlistDailyBrief(BaseModel):
    week_id: date
    trade_date: date
    items: list[DailyBriefItem] = Field(max_length=5)


class WeeklyReviewItem(BaseModel):
    stock_code: str = Field(pattern=r"^\d{6}$")
    stock_name: str
    rank: int = Field(ge=1, le=5)
    entry_price: float = Field(gt=0)
    week_high_return: float
    week_close_return: float
    max_drawdown_from_entry: float
    max_peak_to_trough_drawdown: float
    target_touched: bool
    target_touch_date: date | None = None
    drawdown_before_touch: float | None = None
    accessible_at_entry: bool
    benchmark_return: float | None = None
    benchmark_excess: float | None = None
    industry_return: float | None = None
    industry_excess: float | None = None


class WatchlistWeeklyReview(BaseModel):
    week_id: date
    generated_at: datetime
    items: list[WeeklyReviewItem] = Field(max_length=5)


class WeeklyReviewResponse(BaseModel):
    id: str
    week_id: date
    source_type: Literal["rule", "ai", "published", "historical_replay"]
    source_version: int = Field(ge=1)
    rule_version: str
    status: Literal["completed", "degraded", "failed"]
    entry_trade_date: date
    final_trade_date: date
    as_of: datetime
    generated_at: datetime
    quality: DataQuality
    aggregate: dict[str, object]
    summary: str
    warnings: list[str]
    items: list[WeeklyReviewItem] = Field(max_length=5)


class HistoricalReplayResponse(BaseModel):
    id: str
    week_id: date
    rule_version: str
    status: Literal["completed", "degraded", "failed"]
    decision_cutoff: datetime
    simulated_selection_at: datetime
    simulated_review_at: datetime
    actual_run_at: datetime
    quality: DataQuality
    selected_codes: list[str] = Field(max_length=5)
    daily_briefs: list[DailyBrief]
    warnings: list[str]
    review: WeeklyReviewResponse


class ReplayStageResponse(BaseModel):
    id: str
    stage: Literal["weekly_selection", "daily_brief", "weekly_review"]
    trade_date: date | None = None
    status: Literal["queued", "running", "succeeded", "failed", "skipped"]
    information_cutoff: datetime
    actual_run_at: datetime | None = None
    input_fingerprint: str
    warnings: list[str]
    error_code: str | None = None
    error_message: str | None = None
    details: dict[str, object] = Field(default_factory=dict)
    items: list[dict[str, object]] = Field(default_factory=list)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ReplayRunResponse(BaseModel):
    id: str
    week_id: date
    requested_stage: Literal["weekly_selection", "daily_brief", "weekly_review"]
    trade_date: date | None = None
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    rule_version: str
    effective_rule_version: str
    information_cutoff: datetime
    simulated_selection_at: datetime | None = None
    simulated_review_at: datetime | None = None
    simulated_trade_date: date | None = None
    actual_run_at: datetime
    input_fingerprint: str
    warnings: list[str]
    details: dict[str, object] = Field(default_factory=dict)
    stages: list[ReplayStageResponse]


class ReplayEligibilityResponse(BaseModel):
    week_id: date
    stage: Literal["weekly_selection", "daily_brief", "weekly_review"]
    trade_dates: list[date] = Field(default_factory=list)
    formal_available: bool
    replay_available: bool
    reason: str


class CalendarPreparationRequest(BaseModel):
    week_id: date

    @model_validator(mode="after")
    def validate_week(self) -> "CalendarPreparationRequest":
        if self.week_id.weekday() != 0:
            raise ValueError("week_id must be the natural week's Monday")
        return self


class CalendarPreparationResponse(BaseModel):
    week_id: date
    status: Literal["ready", "refreshed", "unavailable"]
    quality: DataQuality | None = None
    trade_dates: list[date] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ReplayJobRequest(BaseModel):
    stage: Literal["weekly_selection", "daily_brief", "weekly_review"]
    week_id: date
    trade_date: date | None = None
    fill_missing: bool = False
    idempotency_key: str = Field(min_length=8, max_length=64)

    @model_validator(mode="after")
    def validate_target(self) -> "ReplayJobRequest":
        if self.week_id.weekday() != 0:
            raise ValueError("week_id must be the natural week's Monday")
        if self.stage == "daily_brief":
            if self.trade_date is None and not self.fill_missing:
                raise ValueError("trade_date or fill_missing is required for a daily replay")
            if self.trade_date is not None and not (
                self.week_id <= self.trade_date <= self.week_id + timedelta(days=6)
            ):
                raise ValueError("trade_date must belong to week_id")
        elif self.trade_date is not None or self.fill_missing:
            raise ValueError("trade_date/fill_missing is only valid for a daily replay")
        return self


AI_CAPABILITIES = (
    "weekly_selection",
    "weekly_review",
    "error_attribution",
    "rule_evolution",
)
ATTRIBUTION_TAXONOMY = (
    "market_state_error",
    "rotation_lag",
    "continuation_overreach",
    "overheat_filter_loose",
    "overheat_filter_strict",
    "stock_selection_error",
    "catalyst_error",
    "confirmation_insufficient",
    "data_anomaly",
    "candidate_coverage_insufficient",
    "anchor_distortion",
    "ai_swap_error",
    "human_override_error",
)


class AITaskRequest(BaseModel):
    capability: Literal["weekly_selection", "weekly_review", "error_attribution", "rule_evolution"]
    week_id: date | None = None
    replay_run_id: str | None = None
    review_id: str | None = None

    @model_validator(mode="after")
    def validate_subject(self) -> "AITaskRequest":
        if (
            self.capability == "weekly_selection"
            and self.week_id is None
            and self.replay_run_id is None
        ):
            raise ValueError("weekly_selection requires week_id or replay_run_id")
        if (
            self.capability == "weekly_review"
            and self.review_id is None
            and self.week_id is None
            and self.replay_run_id is None
        ):
            raise ValueError("weekly_review requires review_id, week_id, or replay_run_id")
        if (
            self.capability == "error_attribution"
            and self.review_id is None
            and self.week_id is None
        ):
            raise ValueError("error_attribution requires review_id or week_id")
        if self.capability == "rule_evolution" and self.week_id is None:
            raise ValueError("rule_evolution requires week_id")
        return self


class AIConnectionUpdateRequest(BaseModel):
    api_key: str = Field(min_length=20, max_length=512)
    model: str = Field(default="gpt-5.6-sol", min_length=3, max_length=96)


class AIConnectionResponse(BaseModel):
    connected: bool
    source: Literal["personal_api_key", "system_api_key", "none"]
    provider: Literal["openai"] = "openai"
    key_hint: str | None = None
    model: str
    capabilities: dict[str, bool]
    updated_at: datetime | None = None


class AIInvocationResponse(BaseModel):
    id: str
    capability: Literal["weekly_selection", "weekly_review", "error_attribution", "rule_evolution"]
    subject_type: str
    subject_id: str
    provider: str
    model: str
    prompt_hash: str
    schema_version: str
    policy_version: str
    input_fingerprint: str
    status: str
    structured_output: dict[str, object] | None = None
    usage: dict[str, object] = Field(default_factory=dict)
    latency_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class AIAuditResponse(BaseModel):
    id: str
    invocation_id: str
    capability: str
    subject_type: str
    subject_id: str
    input_fingerprint: str
    output_hash: str | None = None
    validation: dict[str, object]
    warnings: list[str]
    created_at: datetime


class AIResolutionRequest(BaseModel):
    action: Literal["confirm", "reject"]
    reason: str = Field(min_length=8, max_length=500)


class AIResolutionResponse(BaseModel):
    attribution_id: str
    action: Literal["confirm", "reject"]
    reason: str
    created_at: datetime


class ErrorAttributionResponse(BaseModel):
    id: str
    week_id: date
    review_id: str | None = None
    taxonomy: Literal[
        "market_state_error",
        "rotation_lag",
        "continuation_overreach",
        "overheat_filter_loose",
        "overheat_filter_strict",
        "stock_selection_error",
        "catalyst_error",
        "confirmation_insufficient",
        "data_anomaly",
        "candidate_coverage_insufficient",
        "anchor_distortion",
        "ai_swap_error",
        "human_override_error",
    ]
    confidence: str
    facts: dict[str, object]
    proposed_hypothesis: str
    counterfactual_allowed: bool
    input_fingerprint: str
    status: Literal["proposed", "confirmed", "rejected"]
    created_at: datetime
    updated_at: datetime


class AIProposalResponse(BaseModel):
    attribution_id: str
    proposal_id: str | None = None
    status: Literal["proposed", "rejected"]
    reason: str | None = None
    created_at: datetime


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    environment: str
    ai_enabled: bool
    ai_model: str


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=256)


class UserResponse(BaseModel):
    id: str
    username: str
    role: Literal["admin", "viewer"]
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class SessionResponse(BaseModel):
    user: UserResponse


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)
    role: Literal["viewer"] = "viewer"


class UpdateUserRequest(BaseModel):
    is_active: bool


class WeeklySelectionJobRequest(BaseModel):
    week_id: date
    idempotency_key: str = Field(min_length=8, max_length=64)

    @model_validator(mode="after")
    def validate_week_id(self) -> "WeeklySelectionJobRequest":
        if self.week_id.weekday() != 0:
            raise ValueError("week_id must be the natural week's Monday")
        return self


class ManualOutputJobRequest(BaseModel):
    job_type: Literal["daily_brief", "weekly_review"]
    week_id: date
    trade_date: date | None = None
    idempotency_key: str = Field(min_length=8, max_length=64)

    @model_validator(mode="after")
    def validate_target(self) -> "ManualOutputJobRequest":
        if self.week_id.weekday() != 0:
            raise ValueError("week_id must be the natural week's Monday")
        if self.job_type == "daily_brief":
            if self.trade_date is None:
                raise ValueError("trade_date is required for a daily brief job")
            if not self.week_id <= self.trade_date <= self.week_id + timedelta(days=6):
                raise ValueError("trade_date must belong to week_id")
        elif self.trade_date is not None:
            raise ValueError("trade_date is only valid for a daily brief job")
        return self


class JobResponse(BaseModel):
    id: str
    job_type: Literal["weekly_selection", "daily_brief", "weekly_review", "replay"]
    week_id: date
    mode: Literal["formal", "replay"] = "formal"
    replay_stage: Literal["weekly_selection", "daily_brief", "weekly_review"] | None = None
    trade_date: date | None = None
    replay_run_id: str | None = None
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    stage: str
    error_code: str | None = None
    error_message: str | None = None
    progress_percent: int = Field(default=0, ge=0, le=100)
    cancel_requested_at: datetime | None = None
    details: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
