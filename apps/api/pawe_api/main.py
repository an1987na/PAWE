import uuid
from datetime import UTC, date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from pawe_api.ai.credentials import (
    AICredentialError,
    delete_user_credential,
    get_user_credential,
    save_user_credential,
)
from pawe_api.ai.repository import (
    attribution_response,
    get_attribution,
    get_invocation,
    list_attributions,
    list_audits,
    resolve_attribution,
)
from pawe_api.ai.service import AIDomainError, AIService
from pawe_api.auth.dependencies import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    AdminCsrfPrincipal,
    AdminPrincipal,
    AuthApplicationDependency,
    CsrfPrincipal,
    CurrentPrincipal,
)
from pawe_api.auth.repository import DuplicateUsernameError, LastAdminError
from pawe_api.briefs.repository import BriefApplication, SqlBriefApplication
from pawe_api.config import get_settings
from pawe_api.contracts import (
    AIAuditResponse,
    AIConnectionResponse,
    AIConnectionUpdateRequest,
    AIInvocationResponse,
    AIProposalResponse,
    AIResolutionRequest,
    AIResolutionResponse,
    AITaskRequest,
    ApprovalRequest,
    ApprovalResponse,
    CalendarPreparationRequest,
    CalendarPreparationResponse,
    CreateUserRequest,
    DailyBrief,
    DecisionVersionResponse,
    ErrorAttributionResponse,
    ExperimentActionRequest,
    ExperimentApprovalRequest,
    ExperimentResponse,
    ExperimentRunCompleteRequest,
    ExperimentRunResponse,
    ExperimentRunStartRequest,
    ExperimentRunUpdateResponse,
    FeatureArtifactResponse,
    HealthResponse,
    HistoricalReplayResponse,
    JobResponse,
    LoginRequest,
    ManualOutputJobRequest,
    PublishRequest,
    ReplayEligibilityResponse,
    ReplayJobRequest,
    ReplayRunResponse,
    RuleProposalRequest,
    RuleProposalResponse,
    RuleProposalValidationRequest,
    SessionResponse,
    SourceCapabilityResponse,
    StockSearchResult,
    UpdateUserRequest,
    UserResponse,
    WatchlistAddRequest,
    WatchlistDailyBrief,
    WatchlistItemResponse,
    WatchlistWeeklyReview,
    WeeklyReviewResponse,
    WeeklySelectionJobRequest,
    WeekSummary,
)
from pawe_api.db import models
from pawe_api.db.session import SessionFactory, get_db_session
from pawe_api.decisions.ledger import DecisionConflictError, DecisionValidationError
from pawe_api.decisions.repository import DecisionApplication, SqlDecisionApplication
from pawe_api.evaluation.repository import SqlWeeklyReviewApplication, WeeklyReviewApplication
from pawe_api.experiments.governance import (
    GovernanceApplication,
    SqlGovernanceApplication,
    map_governance_error,
)
from pawe_api.experiments.historical_week import HistoricalWeekReplayApplication
from pawe_api.jobs.repository import JobApplication, SqlJobApplication
from pawe_api.replay_stage.repository import (
    ReplayApplication,
    ReplayValidationError,
    SqlReplayApplication,
)
from pawe_api.watchlist.repository import SqlWatchlistApplication, WatchlistError

app = FastAPI(
    title="PAWE API",
    version="0.1.0",
    description="Auditable weekly A-share research API; not trading advice.",
)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.web_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)


def get_decision_application(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DecisionApplication:
    return SqlDecisionApplication(session)


DecisionApplicationDependency = Annotated[DecisionApplication, Depends(get_decision_application)]


def get_job_application(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> JobApplication:
    return SqlJobApplication(session)


JobApplicationDependency = Annotated[JobApplication, Depends(get_job_application)]


def get_replay_application(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ReplayApplication:
    return SqlReplayApplication(session)


ReplayApplicationDependency = Annotated[ReplayApplication, Depends(get_replay_application)]


def get_ai_service() -> AIService:
    return AIService(settings)


AIServiceDependency = Annotated[AIService, Depends(get_ai_service)]


def _ai_capabilities(personal_connected: bool) -> dict[str, bool]:
    names = ("weekly_selection", "weekly_review", "error_attribution", "rule_evolution")
    return {
        name: personal_connected
        or bool(
            settings.ai_enabled
            and settings.openai_api_key
            and getattr(settings, f"ai_{name}_enabled")
        )
        for name in names
    }


def get_brief_application(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BriefApplication:
    return SqlBriefApplication(session)


BriefApplicationDependency = Annotated[BriefApplication, Depends(get_brief_application)]


def get_weekly_review_application(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> WeeklyReviewApplication:
    return SqlWeeklyReviewApplication(session)


WeeklyReviewApplicationDependency = Annotated[
    WeeklyReviewApplication, Depends(get_weekly_review_application)
]


def get_watchlist_application(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlWatchlistApplication:
    return SqlWatchlistApplication(session)


WatchlistApplicationDependency = Annotated[
    SqlWatchlistApplication, Depends(get_watchlist_application)
]


def get_governance_application(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> GovernanceApplication:
    return SqlGovernanceApplication(session)


GovernanceApplicationDependency = Annotated[
    GovernanceApplication, Depends(get_governance_application)
]


@app.get("/api/v1/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        environment=settings.env,
        ai_enabled=settings.ai_enabled and bool(settings.openai_api_key),
        ai_model=settings.openai_model,
    )


@app.get(
    "/api/v1/health/source-capabilities",
    response_model=list[SourceCapabilityResponse],
)
async def source_capabilities(
    governance: GovernanceApplicationDependency,
) -> list[SourceCapabilityResponse]:
    return await governance.source_capabilities()


@app.get("/api/v1/health/features", response_model=list[FeatureArtifactResponse])
async def feature_artifacts(
    governance: GovernanceApplicationDependency,
) -> list[FeatureArtifactResponse]:
    return await governance.feature_artifacts()


@app.get("/api/v1/experiments", response_model=list[ExperimentResponse])
async def list_experiments(
    _principal: CurrentPrincipal,
    governance: GovernanceApplicationDependency,
) -> list[ExperimentResponse]:
    return await governance.list_experiments()


@app.post(
    "/api/v1/experiments/rule-proposals",
    response_model=RuleProposalResponse,
    status_code=201,
)
async def create_rule_proposal(
    request: RuleProposalRequest,
    principal: AdminCsrfPrincipal,
    governance: GovernanceApplicationDependency,
) -> RuleProposalResponse:
    try:
        return await governance.create_proposal(request, uuid.UUID(principal.user.id))
    except Exception as exc:
        status_code, detail = map_governance_error(exc)
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.post(
    "/api/v1/experiments/rule-proposals/{proposal_id}/validate",
    response_model=RuleProposalResponse | ExperimentResponse,
)
async def validate_rule_proposal_endpoint(
    proposal_id: uuid.UUID,
    request: RuleProposalValidationRequest,
    _principal: AdminCsrfPrincipal,
    governance: GovernanceApplicationDependency,
) -> RuleProposalResponse | ExperimentResponse:
    try:
        return await governance.validate_proposal(proposal_id, request.expected_version)
    except Exception as exc:
        status_code, detail = map_governance_error(exc)
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.post(
    "/api/v1/experiments/{experiment_id}/replays",
    response_model=ExperimentRunResponse,
    status_code=201,
)
async def queue_experiment_replay(
    experiment_id: uuid.UUID,
    request: ExperimentActionRequest,
    principal: AdminCsrfPrincipal,
    governance: GovernanceApplicationDependency,
) -> ExperimentRunResponse:
    if request.input_fingerprint is None:
        raise HTTPException(status_code=422, detail="input_fingerprint is required")
    try:
        return await governance.queue_run(
            experiment_id,
            "replay",
            request.expected_version,
            request.input_fingerprint,
            uuid.UUID(principal.user.id),
        )
    except Exception as exc:
        status_code, detail = map_governance_error(exc)
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.post(
    "/api/v1/experiments/{experiment_id}/shadow",
    response_model=ExperimentRunResponse,
    status_code=201,
)
async def start_experiment_shadow(
    experiment_id: uuid.UUID,
    request: ExperimentActionRequest,
    principal: AdminCsrfPrincipal,
    governance: GovernanceApplicationDependency,
) -> ExperimentRunResponse:
    if request.input_fingerprint is None:
        raise HTTPException(status_code=422, detail="input_fingerprint is required")
    try:
        return await governance.queue_run(
            experiment_id,
            "shadow",
            request.expected_version,
            request.input_fingerprint,
            uuid.UUID(principal.user.id),
        )
    except Exception as exc:
        status_code, detail = map_governance_error(exc)
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.post(
    "/api/v1/experiments/runs/{run_id}/start",
    response_model=ExperimentRunUpdateResponse,
)
async def start_experiment_run(
    run_id: uuid.UUID,
    request: ExperimentRunStartRequest,
    _principal: AdminCsrfPrincipal,
    governance: GovernanceApplicationDependency,
) -> ExperimentRunUpdateResponse:
    try:
        return await governance.start_run(run_id, request.expected_version)
    except Exception as exc:
        status_code, detail = map_governance_error(exc)
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.post(
    "/api/v1/experiments/runs/{run_id}/complete",
    response_model=ExperimentRunUpdateResponse,
)
async def complete_experiment_run(
    run_id: uuid.UUID,
    request: ExperimentRunCompleteRequest,
    _principal: AdminCsrfPrincipal,
    governance: GovernanceApplicationDependency,
) -> ExperimentRunUpdateResponse:
    try:
        return await governance.complete_run(run_id, request)
    except Exception as exc:
        status_code, detail = map_governance_error(exc)
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.post(
    "/api/v1/experiments/{experiment_id}/shadow-ready",
    response_model=ExperimentResponse,
)
async def prepare_experiment_shadow(
    experiment_id: uuid.UUID,
    request: ExperimentActionRequest,
    _principal: AdminCsrfPrincipal,
    governance: GovernanceApplicationDependency,
) -> ExperimentResponse:
    try:
        return await governance.prepare_shadow(
            experiment_id, request.expected_version, request.reason
        )
    except Exception as exc:
        status_code, detail = map_governance_error(exc)
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.post(
    "/api/v1/experiments/{experiment_id}/approval",
    response_model=ExperimentResponse,
)
async def approve_experiment(
    experiment_id: uuid.UUID,
    request: ExperimentApprovalRequest,
    principal: AdminCsrfPrincipal,
    governance: GovernanceApplicationDependency,
) -> ExperimentResponse:
    try:
        return await governance.approve(experiment_id, request, uuid.UUID(principal.user.id))
    except Exception as exc:
        status_code, detail = map_governance_error(exc)
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.post(
    "/api/v1/experiments/{experiment_id}/activate",
    response_model=ExperimentResponse,
)
async def activate_experiment(
    experiment_id: uuid.UUID,
    request: ExperimentActionRequest,
    _principal: AdminCsrfPrincipal,
    governance: GovernanceApplicationDependency,
) -> ExperimentResponse:
    if not settings.experiment_activation_enabled:
        raise HTTPException(status_code=409, detail="Experiment activation is disabled")
    try:
        return await governance.activate(experiment_id, request.expected_version, request.reason)
    except Exception as exc:
        status_code, detail = map_governance_error(exc)
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.post(
    "/api/v1/experiments/{experiment_id}/rollback",
    response_model=ExperimentResponse,
)
async def rollback_experiment(
    experiment_id: uuid.UUID,
    request: ExperimentActionRequest,
    _principal: AdminCsrfPrincipal,
    governance: GovernanceApplicationDependency,
) -> ExperimentResponse:
    try:
        return await governance.rollback(experiment_id, request.expected_version, request.reason)
    except Exception as exc:
        status_code, detail = map_governance_error(exc)
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.get("/api/v1/weeks/current", response_model=WeekSummary)
async def current_week(
    _principal: CurrentPrincipal,
    decisions: DecisionApplicationDependency,
) -> WeekSummary:
    published = await decisions.current_published(datetime.now(ZoneInfo("Asia/Shanghai")).date())
    if published is None:
        raise HTTPException(status_code=404, detail="No published week is available")
    return published


@app.get("/api/v1/weeks/{week_id}/briefs", response_model=list[DailyBrief])
async def weekly_briefs(
    week_id: date,
    _principal: CurrentPrincipal,
    briefs: BriefApplicationDependency,
) -> list[DailyBrief]:
    return await briefs.list_week(week_id)


@app.get("/api/v1/stocks/search", response_model=list[StockSearchResult])
async def search_stocks(
    q: str,
    principal: CurrentPrincipal,
    watchlist: WatchlistApplicationDependency,
) -> list[StockSearchResult]:
    return await watchlist.search(uuid.UUID(principal.user.id), q)


@app.get("/api/v1/me/watchlist", response_model=list[WatchlistItemResponse])
async def my_watchlist(
    principal: CurrentPrincipal,
    watchlist: WatchlistApplicationDependency,
) -> list[WatchlistItemResponse]:
    return await watchlist.list_active(uuid.UUID(principal.user.id))


@app.post("/api/v1/me/watchlist", response_model=WatchlistItemResponse, status_code=201)
async def add_to_my_watchlist(
    request: WatchlistAddRequest,
    principal: CsrfPrincipal,
    watchlist: WatchlistApplicationDependency,
) -> WatchlistItemResponse:
    try:
        return await watchlist.add(
            uuid.UUID(principal.user.id), request.stock_code, now=datetime.now(ZoneInfo("UTC"))
        )
    except WatchlistError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/v1/me/watchlist/{stock_code}", status_code=204)
async def remove_from_my_watchlist(
    stock_code: str,
    principal: CsrfPrincipal,
    watchlist: WatchlistApplicationDependency,
) -> None:
    removed = await watchlist.remove(
        uuid.UUID(principal.user.id), stock_code, now=datetime.now(ZoneInfo("UTC"))
    )
    if not removed:
        raise HTTPException(status_code=404, detail="Watchlist item not found")


@app.get(
    "/api/v1/me/watchlist/weeks/{week_id}/briefs",
    response_model=list[WatchlistDailyBrief],
)
async def my_watchlist_briefs(
    week_id: date,
    principal: CurrentPrincipal,
    watchlist: WatchlistApplicationDependency,
) -> list[WatchlistDailyBrief]:
    return await watchlist.list_daily(uuid.UUID(principal.user.id), week_id)


@app.get(
    "/api/v1/me/watchlist/weeks/{week_id}/review",
    response_model=WatchlistWeeklyReview,
)
async def my_watchlist_review(
    week_id: date,
    principal: CurrentPrincipal,
    watchlist: WatchlistApplicationDependency,
) -> WatchlistWeeklyReview:
    review = await watchlist.list_weekly(uuid.UUID(principal.user.id), week_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Watchlist review is not available")
    return review


@app.get("/api/v1/reviews/latest", response_model=WeeklyReviewResponse)
async def latest_weekly_review(
    _principal: CurrentPrincipal,
    reviews: WeeklyReviewApplicationDependency,
) -> WeeklyReviewResponse:
    review = await reviews.latest()
    if review is None:
        raise HTTPException(status_code=404, detail="No weekly review is available")
    return review


@app.get("/api/v1/reviews", response_model=list[WeeklyReviewResponse])
async def all_weekly_reviews(
    _principal: CurrentPrincipal,
    reviews: WeeklyReviewApplicationDependency,
) -> list[WeeklyReviewResponse]:
    return await reviews.list_all()


@app.get("/api/v1/replays/eligible-weeks", response_model=list[ReplayEligibilityResponse])
async def replay_eligible_weeks(
    _principal: AdminPrincipal,
    replays: ReplayApplicationDependency,
) -> list[ReplayEligibilityResponse]:
    return await replays.list_eligible_weeks(datetime.now(UTC))


@app.post(
    "/api/v1/replays/prepare-calendar",
    response_model=CalendarPreparationResponse,
)
async def prepare_replay_calendar(
    request: CalendarPreparationRequest,
    _principal: AdminCsrfPrincipal,
    replays: ReplayApplicationDependency,
) -> CalendarPreparationResponse:
    return await replays.prepare_calendar(request.week_id)


@app.get("/api/v1/replay-runs/{run_id}", response_model=ReplayRunResponse)
async def replay_run(
    run_id: uuid.UUID,
    _principal: CurrentPrincipal,
    replays: ReplayApplicationDependency,
) -> ReplayRunResponse:
    result = await replays.get_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Replay run not found")
    return result


@app.get("/api/v1/replay-jobs/{job_id}", response_model=JobResponse)
async def replay_job(
    job_id: uuid.UUID,
    _principal: CurrentPrincipal,
    replays: ReplayApplicationDependency,
) -> JobResponse:
    result = await replays.get_job(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Replay job not found")
    return result


@app.get("/api/v1/weeks/{week_id}/replays", response_model=list[ReplayRunResponse])
async def week_replays(
    week_id: date,
    _principal: CurrentPrincipal,
    replays: ReplayApplicationDependency,
) -> list[ReplayRunResponse]:
    return await replays.list_week(week_id)


@app.get("/api/v1/ai/connection", response_model=AIConnectionResponse)
async def ai_connection(
    principal: CurrentPrincipal,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AIConnectionResponse:
    row = await get_user_credential(session, uuid.UUID(principal.user.id))
    personal_connected = row is not None
    system_connected = bool(settings.ai_enabled and settings.openai_api_key)
    source = (
        "personal_api_key"
        if personal_connected
        else "system_api_key"
        if system_connected
        else "none"
    )
    return AIConnectionResponse(
        connected=personal_connected or system_connected,
        source=source,
        key_hint=row.key_hint if row else None,
        model=row.model if row else settings.openai_model,
        capabilities=_ai_capabilities(personal_connected),
        updated_at=row.updated_at if row else None,
    )


@app.post("/api/v1/ai/connection", response_model=AIConnectionResponse)
async def save_ai_connection(
    request: AIConnectionUpdateRequest,
    principal: CsrfPrincipal,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AIConnectionResponse:
    try:
        row = await save_user_credential(
            session,
            uuid.UUID(principal.user.id),
            request.api_key,
            request.model,
            settings,
        )
    except AICredentialError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AIConnectionResponse(
        connected=True,
        source="personal_api_key",
        key_hint=row.key_hint,
        model=row.model,
        capabilities=_ai_capabilities(True),
        updated_at=row.updated_at,
    )


@app.delete("/api/v1/ai/connection", status_code=204)
async def remove_ai_connection(
    principal: CsrfPrincipal,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    await delete_user_credential(session, uuid.UUID(principal.user.id))
    return Response(status_code=204)


@app.post(
    "/api/v1/ai/tasks",
    response_model=AIInvocationResponse | AIProposalResponse | ErrorAttributionResponse,
    status_code=201,
)
async def run_ai_task(
    request: AITaskRequest,
    principal: CsrfPrincipal,
    ai: AIServiceDependency,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AIInvocationResponse | AIProposalResponse | ErrorAttributionResponse:
    try:
        actor_id = uuid.UUID(principal.user.id)
        if (
            request.capability in {"weekly_selection", "rule_evolution"}
            and principal.user.role != "admin"
        ):
            raise HTTPException(status_code=403, detail="Administrator access required")
        if request.capability == "weekly_selection":
            return await ai.weekly_selection(
                session,
                week_id=request.week_id,
                replay_run_id=uuid.UUID(request.replay_run_id) if request.replay_run_id else None,
                actor_id=actor_id,
            )
        if request.capability == "weekly_review":
            review_id = request.review_id
            if review_id is None and request.week_id is not None:
                review = await session.scalar(
                    select(models.WeeklyReview)
                    .where(models.WeeklyReview.week_id == request.week_id)
                    .order_by(models.WeeklyReview.generated_at.desc())
                    .limit(1)
                )
                review_id = str(review.id) if review else None
            if review_id is None:
                raise AIDomainError("weekly review not found")
            return await ai.weekly_review(
                session, review_id=uuid.UUID(review_id), actor_id=actor_id
            )
        if request.capability == "error_attribution":
            review_id = request.review_id
            if review_id is None and request.week_id is not None:
                review = await session.scalar(
                    select(models.WeeklyReview)
                    .where(models.WeeklyReview.week_id == request.week_id)
                    .order_by(models.WeeklyReview.generated_at.desc())
                    .limit(1)
                )
                review_id = str(review.id) if review else None
            if review_id is None:
                raise AIDomainError("weekly review not found")
            row = await ai.error_attribution(
                session, review_id=uuid.UUID(review_id), actor_id=actor_id
            )
            return attribution_response(row)
        if request.week_id is None:
            raise AIDomainError("week_id is required")
        return await ai.rule_evolution(session, week_id=request.week_id, actor_id=actor_id)
    except (AIDomainError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/ai/invocations/{invocation_id}", response_model=AIInvocationResponse)
async def ai_invocation(
    invocation_id: uuid.UUID,
    _principal: CurrentPrincipal,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AIInvocationResponse:
    result = await get_invocation(session, invocation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="AI invocation not found")
    return result


@app.get("/api/v1/ai/audits", response_model=list[AIAuditResponse])
async def ai_audits(
    _principal: CurrentPrincipal,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    capability: str | None = None,
) -> list[AIAuditResponse]:
    return await list_audits(session, capability=capability)


@app.get("/api/v1/weeks/{week_id}/attributions", response_model=list[ErrorAttributionResponse])
async def week_attributions(
    week_id: date,
    _principal: CurrentPrincipal,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[ErrorAttributionResponse]:
    return await list_attributions(session, week_id)


@app.get("/api/v1/attributions/{attribution_id}", response_model=ErrorAttributionResponse)
async def attribution_detail(
    attribution_id: uuid.UUID,
    _principal: CurrentPrincipal,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ErrorAttributionResponse:
    row = await get_attribution(session, attribution_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Attribution not found")
    return row


@app.post("/api/v1/attributions/{attribution_id}/resolution", response_model=AIResolutionResponse)
async def resolve_week_attribution(
    attribution_id: uuid.UUID,
    request: AIResolutionRequest,
    principal: AdminCsrfPrincipal,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AIResolutionResponse:
    row = await resolve_attribution(
        session, attribution_id, request.action, request.reason, uuid.UUID(principal.user.id)
    )
    if row is None:
        raise HTTPException(status_code=409, detail="Attribution is missing or already resolved")
    return AIResolutionResponse(
        attribution_id=row.id,
        action=request.action,
        reason=request.reason,
        created_at=row.updated_at,
    )


@app.get("/api/v1/history/weeks", response_model=list[date])
async def archived_weeks(
    _principal: CurrentPrincipal,
    reviews: WeeklyReviewApplicationDependency,
) -> list[date]:
    return await reviews.list_archive_weeks()


@app.get("/api/v1/weeks/{week_id}/reviews", response_model=list[WeeklyReviewResponse])
async def weekly_reviews(
    week_id: date,
    _principal: CurrentPrincipal,
    reviews: WeeklyReviewApplicationDependency,
) -> list[WeeklyReviewResponse]:
    return await reviews.list_week(week_id)


@app.get("/api/v1/replays/{week_id}", response_model=HistoricalReplayResponse)
async def historical_replay(
    week_id: date,
    _principal: CurrentPrincipal,
) -> HistoricalReplayResponse:
    replay = await HistoricalWeekReplayApplication(SessionFactory).get(week_id)
    if replay is None:
        raise HTTPException(status_code=404, detail="Historical replay is not available")
    return replay


@app.get("/api/v1/weeks/{week_id}/decisions", response_model=list[DecisionVersionResponse])
async def weekly_decisions(
    week_id: date,
    decisions: DecisionApplicationDependency,
    _principal: CurrentPrincipal,
) -> list[DecisionVersionResponse]:
    return await decisions.list_decisions(week_id)


@app.post("/api/v1/weeks/{week_id}/approval", response_model=ApprovalResponse)
async def approve_week(
    week_id: date,
    request: ApprovalRequest,
    decisions: DecisionApplicationDependency,
    _principal: AdminCsrfPrincipal,
) -> ApprovalResponse:
    try:
        return await decisions.approve(week_id, request)
    except DecisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DecisionValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/weeks/{week_id}/publish", response_model=DecisionVersionResponse)
async def publish_week(
    week_id: date,
    request: PublishRequest,
    decisions: DecisionApplicationDependency,
    _principal: AdminCsrfPrincipal,
) -> DecisionVersionResponse:
    try:
        return await decisions.publish(week_id, request)
    except DecisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DecisionValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/jobs/weekly-selection", response_model=JobResponse, status_code=201)
async def trigger_weekly_selection(
    request: WeeklySelectionJobRequest,
    principal: AdminCsrfPrincipal,
    jobs: JobApplicationDependency,
) -> JobResponse:
    return await jobs.enqueue_weekly_selection(request, uuid.UUID(principal.user.id))


@app.post("/api/v1/jobs/replay", response_model=JobResponse, status_code=201)
async def trigger_replay_job(
    request: ReplayJobRequest,
    principal: AdminCsrfPrincipal,
    replays: ReplayApplicationDependency,
) -> JobResponse:
    try:
        preparation = await replays.prepare_calendar(request.week_id)
        if preparation.status == "unavailable":
            detail = "; ".join(preparation.warnings) or "calendar preparation unavailable"
            raise ReplayValidationError(detail)
        return await replays.enqueue(request, uuid.UUID(principal.user.id), datetime.now(UTC))
    except ReplayValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/jobs/output", response_model=JobResponse, status_code=201)
async def trigger_manual_output(
    request: ManualOutputJobRequest,
    principal: AdminCsrfPrincipal,
    jobs: JobApplicationDependency,
) -> JobResponse:
    return await jobs.enqueue_output_job(request, uuid.UUID(principal.user.id))


@app.get("/api/v1/weeks/{week_id}/jobs", response_model=list[JobResponse])
async def list_week_jobs(
    week_id: date,
    _principal: AdminPrincipal,
    jobs: JobApplicationDependency,
) -> list[JobResponse]:
    return await jobs.list_week_jobs(week_id)


@app.post("/api/v1/jobs/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: uuid.UUID,
    principal: AdminCsrfPrincipal,
    jobs: JobApplicationDependency,
) -> JobResponse:
    job = await jobs.request_cancel(job_id, uuid.UUID(principal.user.id))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/api/v1/auth/login", response_model=SessionResponse)
async def login(
    request: LoginRequest,
    response: Response,
    auth: AuthApplicationDependency,
) -> SessionResponse:
    try:
        issued = await auth.login(request.username, request.password, settings.session_ttl_hours)
    except (ConnectionError, SQLAlchemyError) as exc:
        raise HTTPException(
            status_code=503, detail="Authentication service is temporarily unavailable"
        ) from exc
    if issued is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    response.set_cookie(
        SESSION_COOKIE,
        issued.session_token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        issued.csrf_token,
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
    return SessionResponse(user=issued.user)


@app.get("/api/v1/auth/me", response_model=SessionResponse)
def auth_me(principal: CurrentPrincipal) -> SessionResponse:
    return SessionResponse(user=principal.user)


@app.post("/api/v1/auth/logout", status_code=204)
async def logout(
    response: Response,
    principal: CsrfPrincipal,
    auth: AuthApplicationDependency,
) -> None:
    await auth.logout(principal.session_id)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


@app.get("/api/v1/users", response_model=list[UserResponse])
async def list_users(
    auth: AuthApplicationDependency,
    _principal: AdminPrincipal,
) -> list[UserResponse]:
    return await auth.list_users()


@app.post("/api/v1/users", response_model=UserResponse, status_code=201)
async def create_user(
    request: CreateUserRequest,
    auth: AuthApplicationDependency,
    principal: AdminCsrfPrincipal,
) -> UserResponse:
    try:
        return await auth.create_user(request, uuid.UUID(principal.user.id))
    except DuplicateUsernameError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.patch("/api/v1/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    request: UpdateUserRequest,
    auth: AuthApplicationDependency,
    principal: AdminCsrfPrincipal,
) -> UserResponse:
    try:
        user = await auth.update_user(user_id, request, uuid.UUID(principal.user.id))
    except LastAdminError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user
