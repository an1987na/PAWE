import asyncio
import hashlib
import json
import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from typing import Literal, Protocol, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pawe_api.contracts import (
    CalendarPreparationResponse,
    DataQuality,
    JobResponse,
    ReplayEligibilityResponse,
    ReplayJobRequest,
    ReplayRunResponse,
    ReplayStageResponse,
)
from pawe_api.data.calendar import SHANGHAI
from pawe_api.db import models
from pawe_api.replay_stage.windows import (
    ReplayStage,
    ReplayWindowError,
    classify_replay_window,
    replay_stage_order,
)
from scripts.ingest_exchange_calendar import (
    SSE_2026_URL,
    SZSE_2026_URL,
)
from scripts.ingest_exchange_calendar import (
    ingest as ingest_exchange_calendar,
)


class ReplayValidationError(ValueError):
    pass


_CALENDAR_PREPARE_LOCK = asyncio.Lock()


def due_daily_brief_dates(open_dates: Sequence[date], now: datetime) -> tuple[date, ...]:
    """Return only trading dates whose 15:30 daily deadline has passed."""
    local_now = now.astimezone(SHANGHAI)
    return tuple(
        day
        for day in sorted(set(open_dates))
        if local_now >= datetime.combine(day, time(15, 30), tzinfo=SHANGHAI)
    )


def _calendar_preparation_response(
    week_id: date,
    rows: Sequence[models.TradingCalendar],
    status: Literal["ready", "refreshed"],
) -> CalendarPreparationResponse:
    qualities = {row.quality for row in rows}
    quality = DataQuality(next(iter(qualities))) if len(qualities) == 1 else None
    warnings = [] if quality is DataQuality.VERIFIED else ["CALENDAR_QUALITY_REQUIRES_REVIEW"]
    return CalendarPreparationResponse(
        week_id=week_id,
        status=status,
        quality=quality,
        trade_dates=[row.trade_date for row in rows if row.is_open],
        warnings=warnings,
    )


class ReplayApplication(Protocol):
    async def prepare_calendar(self, week_id: date) -> CalendarPreparationResponse: ...

    async def list_eligible_weeks(self, now: datetime) -> list[ReplayEligibilityResponse]: ...

    async def enqueue(
        self, request: ReplayJobRequest, actor_id: uuid.UUID, now: datetime
    ) -> JobResponse: ...

    async def get_run(self, run_id: uuid.UUID) -> ReplayRunResponse | None: ...

    async def get_job(self, job_id: uuid.UUID) -> JobResponse | None: ...

    async def list_week(self, week_id: date) -> list[ReplayRunResponse]: ...


class SqlReplayApplication:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def prepare_calendar(self, week_id: date) -> CalendarPreparationResponse:
        async with _CALENDAR_PREPARE_LOCK:
            rows = await self._calendar_rows(week_id)
            if len(rows) == 5:
                response = _calendar_preparation_response(week_id, rows, "ready")
                await self.session.rollback()
                return response
            if week_id.year != 2026:
                await self.session.rollback()
                return CalendarPreparationResponse(
                    week_id=week_id,
                    status="unavailable",
                    warnings=["ANNUAL_CALENDAR_SOURCE_NOT_CONFIGURED"],
                )
            try:
                # Use the existing official SSE + SZSE backup ingestion path.
                # It reconciles both manifests and persists explicit quality;
                # no weekday or holiday is inferred locally.
                await self.session.rollback()
                await ingest_exchange_calendar(week_id, week_id.year, SSE_2026_URL, SZSE_2026_URL)
                rows = await self._calendar_rows(week_id)
            except Exception as exc:
                await self.session.rollback()
                return CalendarPreparationResponse(
                    week_id=week_id,
                    status="unavailable",
                    warnings=[f"CALENDAR_PREPARATION_FAILED:{type(exc).__name__}"],
                )
            if len(rows) != 5:
                await self.session.rollback()
                return CalendarPreparationResponse(
                    week_id=week_id,
                    status="unavailable",
                    warnings=["CALENDAR_INCOMPLETE_AFTER_REFRESH"],
                )
            response = _calendar_preparation_response(week_id, rows, "refreshed")
            await self.session.rollback()
            return response

    async def _calendar_rows(self, week_id: date) -> list[models.TradingCalendar]:
        return list(
            await self.session.scalars(
                select(models.TradingCalendar)
                .where(
                    models.TradingCalendar.trade_date >= week_id,
                    models.TradingCalendar.trade_date <= week_id + timedelta(days=4),
                )
                .order_by(models.TradingCalendar.trade_date)
            )
        )

    async def list_eligible_weeks(self, now: datetime) -> list[ReplayEligibilityResponse]:
        rows = list(
            await self.session.scalars(
                select(models.TradingCalendar).order_by(models.TradingCalendar.trade_date)
            )
        )
        grouped: dict[date, list[models.TradingCalendar]] = defaultdict(list)
        for row in rows:
            grouped[row.trade_date - timedelta(days=row.trade_date.weekday())].append(row)
        result: list[ReplayEligibilityResponse] = []
        for week_id, calendar in sorted(grouped.items(), reverse=True):
            if len(calendar) != 5:
                continue
            open_dates = [row.trade_date for row in calendar if row.is_open]
            if len(open_dates) < 3:
                continue
            if any(
                row.quality in {DataQuality.MISSING.value, DataQuality.CONFLICTED.value}
                for row in calendar
            ):
                continue
            next_trading_week_start = next(
                (
                    row.trade_date
                    for row in rows
                    if row.is_open and row.trade_date > week_id + timedelta(days=6)
                ),
                week_id + timedelta(days=7),
            )
            for stage in ReplayStage:
                trade_dates: list[date] = []
                previous_open_date = next(
                    (row.previous_open_date for row in calendar if row.trade_date == open_dates[0]),
                    None,
                )
                try:
                    if stage is ReplayStage.DAILY_BRIEF:
                        trade_dates = list(due_daily_brief_dates(open_dates, now))
                        daily_windows = [
                            classify_replay_window(
                                stage,
                                now=now,
                                week_id=week_id,
                                trade_date=day,
                                first_open_date=open_dates[0],
                                previous_open_date=previous_open_date,
                                final_open_date=open_dates[-1],
                                next_trading_week_start=next_trading_week_start,
                            )
                            for day in trade_dates
                        ]
                        if not daily_windows:
                            result.append(
                                ReplayEligibilityResponse(
                                    week_id=week_id,
                                    stage=stage.value,
                                    trade_dates=[],
                                    formal_available=False,
                                    replay_available=False,
                                    reason="daily brief is not due until 15:30",
                                )
                            )
                            continue
                        result.append(
                            ReplayEligibilityResponse(
                                week_id=week_id,
                                stage=stage.value,
                                trade_dates=trade_dates,
                                formal_available=any(
                                    item.mode == "formal" for item in daily_windows
                                ),
                                replay_available=any(
                                    item.mode == "replay" for item in daily_windows
                                ),
                                reason=(
                                    "after_next_trading_week_started"
                                    if any(item.mode == "replay" for item in daily_windows)
                                    else "current_trading_week_formal"
                                ),
                            )
                        )
                        continue
                    window = classify_replay_window(
                        stage,
                        now=now,
                        week_id=week_id,
                        trade_date=open_dates[-1] if stage is ReplayStage.WEEKLY_REVIEW else None,
                        first_open_date=open_dates[0],
                        previous_open_date=previous_open_date,
                        final_open_date=open_dates[-1],
                        next_trading_week_start=next_trading_week_start,
                    )
                except ReplayWindowError as exc:
                    result.append(
                        ReplayEligibilityResponse(
                            week_id=week_id,
                            stage=stage.value,
                            trade_dates=trade_dates,
                            formal_available=False,
                            replay_available=False,
                            reason=str(exc),
                        )
                    )
                    continue
                result.append(
                    ReplayEligibilityResponse(
                        week_id=week_id,
                        stage=stage.value,
                        trade_dates=trade_dates,
                        formal_available=window.mode == "formal",
                        replay_available=window.mode == "replay",
                        reason=window.reason,
                    )
                )
        return result

    async def enqueue(
        self, request: ReplayJobRequest, actor_id: uuid.UUID, now: datetime
    ) -> JobResponse:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ReplayValidationError("now must be timezone-aware")
        calendar = list(
            await self.session.scalars(
                select(models.TradingCalendar)
                .where(
                    models.TradingCalendar.trade_date >= request.week_id,
                    models.TradingCalendar.trade_date <= request.week_id + timedelta(days=4),
                )
                .order_by(models.TradingCalendar.trade_date)
            )
        )
        if len(calendar) != 5:
            raise ReplayValidationError("replay requires a complete Monday-to-Friday calendar")
        open_dates = [row.trade_date for row in calendar if row.is_open]
        if len(open_dates) < 3:
            raise ReplayValidationError("replay requires at least three trading days")
        stage = ReplayStage(request.stage)
        if (
            stage is ReplayStage.DAILY_BRIEF
            and request.trade_date is not None
            and request.trade_date not in open_dates
        ):
            raise ReplayValidationError("trade_date must be an open trading day")
        target_trade_date = request.trade_date
        if stage is ReplayStage.DAILY_BRIEF and request.fill_missing:
            target_trade_date = None
        due_daily_dates = list(due_daily_brief_dates(open_dates, now))
        if stage is ReplayStage.DAILY_BRIEF and request.fill_missing and not due_daily_dates:
            raise ReplayValidationError("no daily brief date has passed the 15:30 deadline")
        try:
            first_open_row = next(row for row in calendar if row.trade_date == open_dates[0])
            next_trading_week_start = await self.session.scalar(
                select(models.TradingCalendar.trade_date)
                .where(
                    models.TradingCalendar.trade_date > request.week_id + timedelta(days=6),
                    models.TradingCalendar.is_open.is_(True),
                )
                .order_by(models.TradingCalendar.trade_date)
                .limit(1)
            )
            replay_gate_date = target_trade_date
            if stage is ReplayStage.DAILY_BRIEF and request.fill_missing:
                replay_gate_date = due_daily_dates[-1]
            window = classify_replay_window(
                stage,
                now=now,
                week_id=request.week_id,
                trade_date=replay_gate_date or open_dates[-1],
                first_open_date=open_dates[0],
                previous_open_date=first_open_row.previous_open_date,
                final_open_date=open_dates[-1],
                next_trading_week_start=next_trading_week_start,
            )
        except ReplayWindowError as exc:
            raise ReplayValidationError(str(exc)) from exc
        if window.mode != "replay":
            raise ReplayValidationError("the formal window is still open; replay is not eligible")
        input_fingerprint = _fingerprint(
            {
                "week_id": request.week_id.isoformat(),
                "stage": request.stage,
                "trade_date": request.trade_date.isoformat() if request.trade_date else None,
                "fill_missing": request.fill_missing,
                "information_cutoff": window.simulated_cutoff.isoformat(),
            }
        )
        existing = await self.session.scalar(
            select(models.Job)
            .where(
                models.Job.job_type == "replay",
                models.Job.mode == "replay",
                models.Job.idempotency_key == request.idempotency_key,
            )
            .order_by(models.Job.created_at.desc())
            .limit(1)
        )
        if existing is not None:
            return _response(existing)
        replay_id = uuid.uuid4()
        replay = models.ReplayRun(
            id=replay_id,
            week_id=request.week_id,
            requested_stage=request.stage,
            trade_date=request.trade_date,
            status="queued",
            rule_version="v9.0.0",
            effective_rule_version="historical_effective",
            information_cutoff=window.simulated_cutoff,
            simulated_selection_at=datetime.combine(
                open_dates[0], datetime.min.time().replace(hour=8, minute=30), tzinfo=SHANGHAI
            ),
            simulated_review_at=datetime.combine(
                open_dates[-1], datetime.min.time().replace(hour=15, minute=30), tzinfo=SHANGHAI
            ),
            simulated_trade_date=request.trade_date,
            actual_run_at=now,
            input_fingerprint=input_fingerprint,
            idempotency_key=request.idempotency_key,
            warnings=["HISTORICAL_RULE_REGISTRY_UNAVAILABLE_USING_CURRENT_V9"],
            details={"requested_stage": request.stage, "fill_missing": request.fill_missing},
            created_by_user_id=actor_id,
            created_at=now,
            updated_at=now,
        )
        self.session.add(replay)
        # ``Job.replay_run_id`` and every staged row reference this parent by
        # foreign key.  These models intentionally use scalar UUID columns
        # (rather than ORM relationships), so SQLAlchemy cannot infer the
        # dependency ordering during one unit-of-work flush.  Persist the
        # parent first while keeping the same transaction; otherwise
        # PostgreSQL may attempt to insert the job before ``replay_runs`` and
        # reject the request with a foreign-key violation.
        await self.session.flush()
        if stage is ReplayStage.WEEKLY_REVIEW:
            daily_dates = tuple(open_dates)
        elif request.fill_missing:
            daily_dates = tuple(due_daily_dates)
        elif request.trade_date is not None:
            daily_dates = (request.trade_date,)
        else:
            daily_dates = ()
        stages: list[tuple[ReplayStage, date | None]] = []
        for dependency in replay_stage_order(stage):
            if dependency is ReplayStage.DAILY_BRIEF and daily_dates:
                stages.extend((dependency, day) for day in daily_dates)
            else:
                stages.append((dependency, None))
        for stage_value, day in stages:
            cutoff = window.simulated_cutoff
            if stage_value is ReplayStage.DAILY_BRIEF and day is not None:
                cutoff = datetime.combine(
                    day, datetime.min.time().replace(hour=15), tzinfo=SHANGHAI
                )
            if stage_value is ReplayStage.WEEKLY_REVIEW:
                cutoff = datetime.combine(
                    open_dates[-1], datetime.min.time().replace(hour=15, minute=30), tzinfo=SHANGHAI
                )
            self.session.add(
                models.ReplayStageRun(
                    id=uuid.uuid4(),
                    replay_run_id=replay_id,
                    stage=stage_value.value,
                    trade_date=day,
                    status="queued",
                    information_cutoff=cutoff,
                    actual_run_at=None,
                    input_fingerprint=input_fingerprint,
                    warnings=[],
                    error_code=None,
                    error_message=None,
                    details={"dependency": stage_value is not stage},
                    created_at=now,
                    started_at=None,
                    finished_at=None,
                )
            )
        job = models.Job(
            id=uuid.uuid4(),
            job_type="replay",
            mode="replay",
            replay_stage=request.stage,
            trade_date=request.trade_date,
            replay_run_id=replay_id,
            week_id=request.week_id,
            status="queued",
            stage="queued",
            idempotency_key=request.idempotency_key,
            created_by_user_id=actor_id,
            error_code=None,
            error_message=None,
            details={
                "progress_percent": 0,
                "requested_stage": request.stage,
                "fill_missing": request.fill_missing,
                "input_fingerprint": input_fingerprint,
                "warnings": ["HISTORICAL_RULE_REGISTRY_UNAVAILABLE_USING_CURRENT_V9"],
                "events": [],
            },
            created_at=now,
            started_at=None,
            finished_at=None,
        )
        self.session.add(job)
        await self.session.flush()
        await self.session.commit()
        return _response(job)

    async def get_run(self, run_id: uuid.UUID) -> ReplayRunResponse | None:
        run = await self.session.get(models.ReplayRun, run_id)
        if run is None:
            return None
        stages = list(
            await self.session.scalars(
                select(models.ReplayStageRun)
                .where(models.ReplayStageRun.replay_run_id == run_id)
                .order_by(models.ReplayStageRun.created_at, models.ReplayStageRun.trade_date)
            )
        )
        return await _run_response(self.session, run, stages)

    async def get_job(self, job_id: uuid.UUID) -> JobResponse | None:
        job = await self.session.get(models.Job, job_id)
        if job is None or job.mode != "replay":
            return None
        return _response(job)

    async def list_week(self, week_id: date) -> list[ReplayRunResponse]:
        runs = list(
            await self.session.scalars(
                select(models.ReplayRun)
                .where(models.ReplayRun.week_id == week_id)
                .order_by(models.ReplayRun.created_at.desc())
            )
        )
        result: list[ReplayRunResponse] = []
        for run in runs:
            response = await self.get_run(run.id)
            if response is not None:
                result.append(response)
        return result


def _fingerprint(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _response(job: models.Job) -> JobResponse:
    if job.week_id is None:
        raise ReplayValidationError("replay job has no week_id")
    stored = job.details.get("progress_percent")
    return JobResponse(
        id=str(job.id),
        job_type="replay",
        week_id=job.week_id,
        mode="replay",
        replay_stage=cast(str, job.replay_stage),
        trade_date=job.trade_date,
        replay_run_id=str(job.replay_run_id) if job.replay_run_id else None,
        status=job.status,
        stage=job.stage,
        error_code=job.error_code,
        error_message=job.error_message,
        progress_percent=stored if isinstance(stored, int) else 0,
        cancel_requested_at=job.cancel_requested_at,
        details=job.details,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


async def _stage_items(
    session: AsyncSession,
    stage: models.ReplayStageRun,
) -> list[dict[str, object]]:
    if stage.stage == ReplayStage.WEEKLY_SELECTION.value:
        rows = await session.execute(
            select(models.ReplayDecisionItem, models.Stock)
            .join(models.Stock, models.Stock.id == models.ReplayDecisionItem.stock_id)
            .join(
                models.ReplayDecisionSet,
                models.ReplayDecisionSet.id == models.ReplayDecisionItem.replay_decision_set_id,
            )
            .where(models.ReplayDecisionSet.replay_stage_run_id == stage.id)
            .order_by(models.ReplayDecisionItem.rank)
        )
        return [
            {
                "rank": item.rank,
                "stock_code": stock.code,
                "stock_name": stock.name,
                "role": item.role,
                "target_return": float(item.target_return),
                "confidence": item.confidence,
                "summary": item.summary,
                "primary_risk": item.primary_risk,
            }
            for item, stock in rows.all()
        ]
    if stage.stage == ReplayStage.DAILY_BRIEF.value:
        rows = await session.execute(
            select(models.ReplayDailyBriefItem, models.Stock)
            .join(models.Stock, models.Stock.id == models.ReplayDailyBriefItem.stock_id)
            .join(
                models.ReplayDailyBrief,
                models.ReplayDailyBrief.id == models.ReplayDailyBriefItem.replay_daily_brief_id,
            )
            .where(models.ReplayDailyBrief.replay_stage_run_id == stage.id)
            .order_by(models.Stock.code)
        )
        return [
            {
                "stock_code": stock.code,
                "stock_name": stock.name,
                "daily_return": float(item.daily_return),
                "week_to_date_return": float(item.week_to_date_return),
                "week_high_return": float(item.week_high_return),
                "drawdown_from_week_high": float(item.drawdown_from_week_high),
                "distance_to_target": float(item.distance_to_target),
                "volume_activity": (
                    float(item.volume_activity) if item.volume_activity is not None else None
                ),
                "risk_status": item.risk_status,
                "summary": item.summary,
                "evidence_ids": item.evidence_ids,
            }
            for item, stock in rows.all()
        ]
    if stage.stage == ReplayStage.WEEKLY_REVIEW.value:
        rows = await session.execute(
            select(models.ReplayWeeklyReviewItem, models.Stock)
            .join(models.Stock, models.Stock.id == models.ReplayWeeklyReviewItem.stock_id)
            .join(
                models.ReplayWeeklyReview,
                models.ReplayWeeklyReview.id
                == models.ReplayWeeklyReviewItem.replay_weekly_review_id,
            )
            .where(models.ReplayWeeklyReview.replay_stage_run_id == stage.id)
            .order_by(models.ReplayWeeklyReviewItem.rank)
        )
        return [
            {
                "rank": item.rank,
                "stock_code": stock.code,
                "stock_name": stock.name,
                "entry_price": float(item.entry_price),
                "week_high_return": float(item.week_high_return),
                "week_close_return": float(item.week_close_return),
                "max_drawdown_from_entry": float(item.max_drawdown_from_entry),
                "max_peak_to_trough_drawdown": float(item.max_peak_to_trough_drawdown),
                "target_touched": item.target_touched,
                "target_touch_date": item.target_touch_date,
                "drawdown_before_touch": (
                    float(item.drawdown_before_touch)
                    if item.drawdown_before_touch is not None
                    else None
                ),
                "accessible_at_entry": item.accessible_at_entry,
                "benchmark_return": (
                    float(item.benchmark_return) if item.benchmark_return is not None else None
                ),
                "benchmark_excess": (
                    float(item.benchmark_excess) if item.benchmark_excess is not None else None
                ),
                "industry_return": (
                    float(item.industry_return) if item.industry_return is not None else None
                ),
                "industry_excess": (
                    float(item.industry_excess) if item.industry_excess is not None else None
                ),
            }
            for item, stock in rows.all()
        ]
    return []


async def _stage_response(
    session: AsyncSession,
    stage: models.ReplayStageRun,
) -> ReplayStageResponse:
    return ReplayStageResponse(
        id=str(stage.id),
        stage=stage.stage,
        trade_date=stage.trade_date,
        status=stage.status,
        information_cutoff=stage.information_cutoff,
        actual_run_at=stage.actual_run_at,
        input_fingerprint=stage.input_fingerprint,
        warnings=stage.warnings,
        error_code=stage.error_code,
        error_message=stage.error_message,
        details=stage.details,
        items=await _stage_items(session, stage),
        created_at=stage.created_at,
        started_at=stage.started_at,
        finished_at=stage.finished_at,
    )


async def _run_response(
    session: AsyncSession,
    run: models.ReplayRun,
    stages: list[models.ReplayStageRun],
) -> ReplayRunResponse:
    return ReplayRunResponse(
        id=str(run.id),
        week_id=run.week_id,
        requested_stage=run.requested_stage,
        trade_date=run.trade_date,
        status=run.status,
        rule_version=run.rule_version,
        effective_rule_version=run.effective_rule_version,
        information_cutoff=run.information_cutoff,
        simulated_selection_at=run.simulated_selection_at,
        simulated_review_at=run.simulated_review_at,
        simulated_trade_date=run.simulated_trade_date,
        actual_run_at=run.actual_run_at,
        input_fingerprint=run.input_fingerprint,
        warnings=run.warnings,
        details=run.details,
        stages=[await _stage_response(session, stage) for stage in stages],
    )
