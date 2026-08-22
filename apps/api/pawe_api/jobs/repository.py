import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal, Protocol, cast

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pawe_api.contracts import (
    DataQuality,
    JobResponse,
    ManualOutputJobRequest,
    WeeklySelectionJobRequest,
)
from pawe_api.data.baseline import (
    FEATURE_SCHEMA_VERSION,
    STATE_INPUT_SCHEMA_VERSION,
    deserialize_market_state_input,
    deserialize_rule_features,
)
from pawe_api.data.calendar import (
    SHANGHAI,
    TradingCalendarDay,
    assess_trading_week,
    build_trading_week_schedule,
)
from pawe_api.data.classification_repository import (
    SqlClassificationRepository,
    StoredPrimaryClassification,
)
from pawe_api.data.snapshot import FrozenSnapshot
from pawe_api.db import models
from pawe_api.rules.engine import RULE_VERSION, RuleRunResult, run_v9_rules
from pawe_api.rules.models import CandidateBucket, Domain, RuleFeatures, ScoredCandidate


async def _next_trading_week_start(session: AsyncSession, week_id: date) -> datetime:
    next_open = await session.scalar(
        select(models.TradingCalendar.trade_date)
        .where(
            models.TradingCalendar.trade_date > week_id + timedelta(days=6),
            models.TradingCalendar.is_open.is_(True),
        )
        .order_by(models.TradingCalendar.trade_date)
        .limit(1)
    )
    boundary = next_open or (week_id + timedelta(days=7))
    return datetime.combine(boundary, time(0), tzinfo=SHANGHAI)


class JobApplication(Protocol):
    async def list_week_jobs(self, week_id: date) -> list[JobResponse]: ...

    async def enqueue_weekly_selection(
        self,
        request: WeeklySelectionJobRequest,
        actor_id: uuid.UUID,
    ) -> JobResponse: ...

    async def enqueue_output_job(
        self,
        request: ManualOutputJobRequest,
        actor_id: uuid.UUID,
    ) -> JobResponse: ...

    async def trigger_weekly_selection(
        self,
        request: WeeklySelectionJobRequest,
        actor_id: uuid.UUID,
    ) -> JobResponse: ...

    async def request_cancel(
        self, job_id: uuid.UUID, actor_id: uuid.UUID
    ) -> JobResponse | None: ...


class SqlJobApplication:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_week_jobs(self, week_id: date) -> list[JobResponse]:
        rows = await self.session.scalars(
            select(models.Job)
            .where(models.Job.week_id == week_id)
            .order_by(models.Job.created_at.desc())
            .limit(20)
        )
        return [_response(job) for job in rows]

    async def request_cancel(self, job_id: uuid.UUID, actor_id: uuid.UUID) -> JobResponse | None:
        now = datetime.now(UTC)
        async with self.session.begin():
            job = await self.session.scalar(
                select(models.Job).where(models.Job.id == job_id).with_for_update()
            )
            if job is None:
                return None
            if job.status not in {"queued", "running"}:
                return _response(job)
            if job.cancel_requested_at is None:
                job.cancel_requested_at = now
                if job.status == "queued":
                    job.status = "cancelled"
                    job.stage = "cancelled"
                    job.finished_at = now
                stored_progress = job.details.get("progress_percent", 0)
                job.details = _append_progress(
                    job.details,
                    stored_progress if isinstance(stored_progress, int) else 0,
                    job.stage,
                    now,
                    "管理员已请求取消；任务将在当前原子步骤完成后安全停止。",
                ) | {"cancel_requested_by": str(actor_id)}
            return _response(job)

    async def enqueue_weekly_selection(
        self,
        request: WeeklySelectionJobRequest,
        actor_id: uuid.UUID,
    ) -> JobResponse:
        now = datetime.now(UTC)
        try:
            async with self.session.begin():
                existing = await self.session.scalar(
                    select(models.Job).where(
                        models.Job.job_type == "weekly_selection",
                        models.Job.week_id == request.week_id,
                        models.Job.idempotency_key == request.idempotency_key,
                    )
                )
                if existing is not None:
                    return _response(existing)
                completed = await self.session.scalar(
                    select(models.Job)
                    .where(
                        models.Job.job_type == "weekly_selection",
                        models.Job.week_id == request.week_id,
                        models.Job.status == "succeeded",
                    )
                    .order_by(models.Job.created_at.desc())
                    .limit(1)
                    .with_for_update()
                )
                if completed is not None:
                    return _response(completed)
                active = await self.session.scalar(
                    select(models.Job)
                    .where(
                        models.Job.job_type == "weekly_selection",
                        models.Job.week_id == request.week_id,
                        models.Job.status.in_(("queued", "running")),
                    )
                    .with_for_update()
                )
                if active is not None:
                    return _response(active)
                job = models.Job(
                    id=uuid.uuid4(),
                    job_type="weekly_selection",
                    mode="formal",
                    replay_stage=None,
                    trade_date=None,
                    replay_run_id=None,
                    week_id=request.week_id,
                    status="queued",
                    stage="queued",
                    idempotency_key=request.idempotency_key,
                    created_by_user_id=actor_id,
                    error_code=None,
                    error_message=None,
                    details=_progress_details(
                        0,
                        "queued",
                        now,
                        "任务已进入队列，等待后台执行器领取。",
                    ),
                    created_at=now,
                    started_at=None,
                    finished_at=None,
                )
                self.session.add(job)
                await self.session.flush()
                return _response(job)
        except IntegrityError:
            await self.session.rollback()
            active = await self.session.scalar(
                select(models.Job)
                .where(
                    models.Job.job_type == "weekly_selection",
                    models.Job.week_id == request.week_id,
                    models.Job.status.in_(("queued", "running")),
                )
                .order_by(models.Job.created_at.desc())
                .limit(1)
            )
            if active is None:
                raise
            return _response(active)

    async def enqueue_output_job(
        self,
        request: ManualOutputJobRequest,
        actor_id: uuid.UUID,
    ) -> JobResponse:
        now = datetime.now(UTC)
        try:
            async with self.session.begin():
                existing = await self.session.scalar(
                    select(models.Job).where(
                        models.Job.job_type == request.job_type,
                        models.Job.week_id == request.week_id,
                        models.Job.idempotency_key == request.idempotency_key,
                    )
                )
                if existing is not None:
                    return _response(existing)
                completed_output_id = await self.session.scalar(
                    (
                        select(models.DailyBrief.id).where(
                            models.DailyBrief.week_id == request.week_id,
                            models.DailyBrief.trade_date == request.trade_date,
                            models.DailyBrief.status == "published",
                            models.DailyBrief.is_active.is_(True),
                        )
                        if request.job_type == "daily_brief"
                        else select(models.WeeklyReview.id).where(
                            models.WeeklyReview.week_id == request.week_id,
                            models.WeeklyReview.source_type.in_(("rule", "ai", "published")),
                            models.WeeklyReview.status.in_(("completed", "degraded")),
                            models.WeeklyReview.is_active.is_(True),
                        )
                    )
                    .limit(1)
                    .with_for_update()
                )
                completed_job_query = select(models.Job).where(
                    models.Job.job_type == request.job_type,
                    models.Job.week_id == request.week_id,
                    models.Job.status == "succeeded",
                )
                if request.trade_date is not None:
                    completed_job_query = completed_job_query.where(
                        models.Job.details["trade_date"].astext == request.trade_date.isoformat()
                    )
                completed_job = await self.session.scalar(
                    completed_job_query.order_by(models.Job.created_at.desc()).limit(1)
                )
                if completed_output_id is not None and completed_job is not None:
                    return _response(completed_job)
                if completed_output_id is not None:
                    target_label = (
                        request.trade_date.isoformat()
                        if request.trade_date is not None
                        else request.week_id.isoformat()
                    )
                    stage = (
                        "daily_brief_ready"
                        if request.job_type == "daily_brief"
                        else "weekly_review_ready"
                    )
                    message = f"{target_label} 产出已存在，本次未重复执行。"
                    completed_job = models.Job(
                        id=uuid.uuid4(),
                        job_type=request.job_type,
                        mode="formal",
                        replay_stage=None,
                        trade_date=request.trade_date,
                        replay_run_id=None,
                        week_id=request.week_id,
                        status="succeeded",
                        stage=stage,
                        idempotency_key=request.idempotency_key,
                        created_by_user_id=actor_id,
                        error_code=None,
                        error_message=None,
                        details=_progress_details(100, stage, now, message)
                        | {"reused": True}
                        | ({"trade_date": target_label} if request.trade_date else {}),
                        created_at=now,
                        started_at=now,
                        finished_at=now,
                    )
                    self.session.add(completed_job)
                    await self.session.flush()
                    return _response(completed_job)
                active = await self.session.scalar(
                    select(models.Job)
                    .where(
                        models.Job.job_type == request.job_type,
                        models.Job.week_id == request.week_id,
                        models.Job.status.in_(("queued", "running")),
                    )
                    .with_for_update()
                )
                if active is not None:
                    return _response(active)
                target_label = (
                    request.trade_date.isoformat()
                    if request.trade_date is not None
                    else request.week_id.isoformat()
                )
                job = models.Job(
                    id=uuid.uuid4(),
                    job_type=request.job_type,
                    mode="formal",
                    replay_stage=None,
                    trade_date=request.trade_date,
                    replay_run_id=None,
                    week_id=request.week_id,
                    status="queued",
                    stage="queued",
                    idempotency_key=request.idempotency_key,
                    created_by_user_id=actor_id,
                    error_code=None,
                    error_message=None,
                    details=_progress_details(
                        0,
                        "queued",
                        now,
                        f"{target_label} 产出任务已进入队列，等待后台执行器领取。",
                    )
                    | ({"trade_date": target_label} if request.trade_date else {}),
                    created_at=now,
                    started_at=None,
                    finished_at=None,
                )
                self.session.add(job)
                await self.session.flush()
                return _response(job)
        except IntegrityError:
            await self.session.rollback()
            active = await self.session.scalar(
                select(models.Job)
                .where(
                    models.Job.job_type == request.job_type,
                    models.Job.week_id == request.week_id,
                    models.Job.status.in_(("queued", "running")),
                )
                .order_by(models.Job.created_at.desc())
                .limit(1)
            )
            if active is None:
                raise
            return _response(active)

    async def claim_next_output_job(self) -> JobResponse | None:
        async with self.session.begin():
            job = await self.session.scalar(
                select(models.Job)
                .where(
                    models.Job.job_type.in_(("daily_brief", "weekly_review")),
                    models.Job.status == "queued",
                )
                .order_by(models.Job.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            now = datetime.now(UTC)
            job.status = "running"
            job.stage = "daily_data_fetch" if job.job_type == "daily_brief" else "review_gate"
            job.started_at = now
            job.details = _append_progress(
                job.details,
                10,
                job.stage,
                now,
                "后台执行器已领取任务，开始核对生成条件。",
            )
            await self.session.flush()
            return _response(job)

    async def claim_next_replay_job(self) -> JobResponse | None:
        async with self.session.begin():
            job = await self.session.scalar(
                select(models.Job)
                .where(
                    models.Job.job_type == "replay",
                    models.Job.mode == "replay",
                    models.Job.status == "queued",
                )
                .order_by(models.Job.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            now = datetime.now(UTC)
            job.status = "running"
            job.stage = "replay_running"
            job.started_at = now
            job.details = _append_progress(
                job.details,
                10,
                job.stage,
                now,
                "回溯任务已领取，正在按阶段核对依赖。",
            )
            await self.session.flush()
            return _response(job)

    async def finish_output_job(
        self,
        job_id: uuid.UUID,
        *,
        succeeded: bool,
        stage: str,
        message: str,
        details: Mapping[str, object] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> JobResponse:
        async with self.session.begin():
            job = await self.session.scalar(
                select(models.Job).where(models.Job.id == job_id).with_for_update()
            )
            if job is None:
                raise LookupError("output job does not exist")
            finished_at = datetime.now(UTC)
            cancelled = job.cancel_requested_at is not None
            job.status = "cancelled" if cancelled else "succeeded" if succeeded else "failed"
            job.stage = "cancelled" if cancelled else stage
            job.error_code = "JOB_CANCELLED" if cancelled else error_code
            job.error_message = (
                "Job stopped after the current atomic step" if cancelled else error_message
            )
            job.finished_at = finished_at
            job.details = _append_progress(
                job.details | dict(details or {}),
                100,
                job.stage,
                finished_at,
                "任务已在当前原子步骤完成后安全停止。" if cancelled else message,
            )
            await self.session.flush()
            return _response(job)

    async def update_output_job_progress(
        self,
        job_id: uuid.UUID,
        *,
        stage: str,
        percent: int,
        message: str,
    ) -> None:
        async with self.session.begin():
            job = await self.session.scalar(
                select(models.Job).where(models.Job.id == job_id).with_for_update()
            )
            if job is None or job.status != "running":
                return
            job.stage = stage
            job.details = _append_progress(
                job.details,
                percent,
                stage,
                datetime.now(UTC),
                message,
            )

    async def trigger_weekly_selection(
        self,
        request: WeeklySelectionJobRequest,
        actor_id: uuid.UUID,
    ) -> JobResponse:
        async with self.session.begin():
            existing = await self.session.scalar(
                select(models.Job).where(
                    models.Job.job_type == "weekly_selection",
                    models.Job.week_id == request.week_id,
                    models.Job.idempotency_key == request.idempotency_key,
                )
            )
            if existing is not None:
                return _response(existing)

            now = datetime.now(UTC)
            calendar_rows = list(
                await self.session.scalars(
                    select(models.TradingCalendar)
                    .where(
                        models.TradingCalendar.trade_date >= request.week_id,
                        models.TradingCalendar.trade_date <= request.week_id + timedelta(days=4),
                    )
                    .order_by(models.TradingCalendar.trade_date)
                )
            )
            formal_end = await _next_trading_week_start(self.session, request.week_id)
            gate = await self._evaluate_gates(
                request.week_id, calendar_rows, now, formal_end=formal_end
            )
            result = (
                await self._execute_rule(request.week_id, gate, now)
                if gate.ready
                else _JobResult.from_gate(gate)
            )
            job = models.Job(
                id=uuid.uuid4(),
                job_type="weekly_selection",
                mode="formal",
                replay_stage=None,
                trade_date=None,
                replay_run_id=None,
                week_id=request.week_id,
                status=result.status,
                stage=result.stage,
                idempotency_key=request.idempotency_key,
                created_by_user_id=actor_id,
                error_code=result.error_code,
                error_message=result.error_message,
                details=result.details,
                created_at=now,
                started_at=now,
                finished_at=now,
            )
            self.session.add(job)
            await self.session.flush()
            return _response(job)

    async def _evaluate_gates(
        self,
        week_id: date,
        calendar_rows: list[models.TradingCalendar],
        now: datetime,
        progress: "ProgressCallback | None" = None,
        *,
        formal_end: datetime | None = None,
    ) -> "_GateOutcome":
        await _report(progress, "calendar_gate", 10, "正在核对本周交易日历。")
        local_now = now.astimezone(SHANGHAI)
        effective_formal_end = formal_end or datetime.combine(
            week_id + timedelta(days=7), time(0), tzinfo=SHANGHAI
        )
        if local_now >= effective_formal_end:
            return _GateOutcome.failed(
                "publication_gate",
                "FORMAL_WEEK_ENDED",
                "The next trading week has started; use historical replay",
                {"formal_end": effective_formal_end.isoformat()},
            )
        if len(calendar_rows) != 5:
            return _GateOutcome.failed(
                "calendar_gate",
                "CALENDAR_MISSING",
                "The Monday-to-Friday trading calendar is incomplete",
                {"calendar_day_count": len(calendar_rows)},
            )
        try:
            calendar_days = [
                TradingCalendarDay(row.trade_date, row.is_open, DataQuality(row.quality))
                for row in calendar_rows
            ]
        except ValueError:
            return _GateOutcome.failed(
                "calendar_gate",
                "CALENDAR_DEGRADED",
                "The trading calendar contains an unsupported quality value",
                {},
            )
        assessment = assess_trading_week(week_id, calendar_days)
        if not assessment.qualifies:
            code = (
                "CALENDAR_DEGRADED"
                if assessment.reason == "TRADING_CALENDAR_DEGRADED"
                else "CALENDAR_INELIGIBLE"
            )
            return _GateOutcome.failed(
                "calendar_gate",
                code,
                assessment.reason or "The natural week has fewer than three trading days",
                {"trading_day_count": assessment.trading_day_count},
            )
        if assessment.data_quality is DataQuality.DEGRADED:
            return _GateOutcome.failed(
                "calendar_gate",
                "CALENDAR_DEGRADED",
                "A backup-only calendar requires manual resolution",
                {"trading_day_count": assessment.trading_day_count},
            )
        first_open = assessment.first_open_date
        assert first_open is not None
        first_open_row = next(row for row in calendar_rows if row.trade_date == first_open)
        if first_open_row.previous_open_date is None:
            return _GateOutcome.failed(
                "calendar_gate",
                "PREVIOUS_OPEN_DATE_MISSING",
                "The previous trading day is required to establish the decision cutoff",
                {},
            )
        schedule = build_trading_week_schedule(
            assessment,
            previous_open_date=first_open_row.previous_open_date,
        )
        await _report(progress, "publication_gate", 25, "正在核对数据截止点与开盘前窗口。")
        local_now = now.astimezone(SHANGHAI)
        schedule_details: dict[str, object] = {
            "decision_cutoff": schedule.decision_cutoff.isoformat(),
            "publication_deadline": schedule.publication_deadline.isoformat(),
            "formal_end": effective_formal_end.isoformat(),
            "supplemental_generation": local_now >= schedule.publication_deadline,
            "evaluation_entry_date": schedule.evaluation_entry_date.isoformat(),
        }
        if local_now <= schedule.decision_cutoff:
            return _GateOutcome.failed(
                "publication_gate",
                "PREPARATION_WINDOW_NOT_OPEN",
                "Weekly preparation requires the decision-cutoff session to be complete",
                schedule_details,
            )
        await _report(progress, "snapshot_gate", 40, "正在核对冻结数据快照。")
        snapshot = await self.session.scalar(
            select(models.DataSnapshot).where(
                models.DataSnapshot.as_of == schedule.decision_cutoff,
                models.DataSnapshot.locked_at < effective_formal_end,
                models.DataSnapshot.quality.not_in(
                    [DataQuality.CONFLICTED.value, DataQuality.MISSING.value]
                ),
            )
        )
        if snapshot is None:
            return _GateOutcome.failed(
                "snapshot_gate",
                "SNAPSHOT_MISSING",
                "No valid locked snapshot exists at the decision cutoff",
                schedule_details,
            )
        await _report(progress, "feature_gate", 55, "正在核对版本化 V9 特征。")
        feature_count = await self.session.scalar(
            select(func.count(models.WeeklyFeature.id)).where(
                models.WeeklyFeature.snapshot_id == snapshot.id,
                models.WeeklyFeature.feature_version == FEATURE_SCHEMA_VERSION,
            )
        )
        if not feature_count:
            return _GateOutcome.failed(
                "feature_gate",
                "FEATURE_SET_MISSING",
                "The versioned V9 feature set is not available",
                schedule_details | {"snapshot_id": str(snapshot.id)},
            )
        await _report(progress, "state_gate", 65, "正在核对市场状态输入。")
        state_input = await self.session.scalar(
            select(models.WeeklyStateInput.id).where(
                models.WeeklyStateInput.snapshot_id == snapshot.id,
                models.WeeklyStateInput.input_version == STATE_INPUT_SCHEMA_VERSION,
            )
        )
        if state_input is None:
            return _GateOutcome.failed(
                "feature_gate",
                "STATE_INPUT_MISSING",
                "The versioned V9 market-state input is not available",
                schedule_details | {"snapshot_id": str(snapshot.id)},
            )
        return _GateOutcome.ready_for_rule(
            snapshot,
            schedule_details | {"snapshot_id": str(snapshot.id)},
        )

    async def _execute_rule(
        self,
        week_id: date,
        gate: "_GateOutcome",
        now: datetime,
        progress: "ProgressCallback | None" = None,
    ) -> "_JobResult":
        await _report(progress, "rule_execution", 75, "正在运行 V9 规则与组合约束。")
        snapshot = gate.snapshot
        assert snapshot is not None
        rows = list(
            (
                await self.session.execute(
                    select(models.WeeklyFeature, models.Stock)
                    .join(models.Stock, models.Stock.id == models.WeeklyFeature.stock_id)
                    .where(
                        models.WeeklyFeature.snapshot_id == snapshot.id,
                        models.WeeklyFeature.feature_version == FEATURE_SCHEMA_VERSION,
                    )
                )
            ).all()
        )
        state_row = await self.session.scalar(
            select(models.WeeklyStateInput).where(
                models.WeeklyStateInput.snapshot_id == snapshot.id,
                models.WeeklyStateInput.input_version == STATE_INPUT_SCHEMA_VERSION,
            )
        )
        assert state_row is not None
        try:
            features = [deserialize_rule_features(row.payload) for row, _ in rows]
            state_input = deserialize_market_state_input(state_row.payload)
        except (TypeError, ValueError) as exc:
            return _JobResult.failure(
                "feature_gate",
                "INPUT_PAYLOAD_INVALID",
                f"Stored V9 input payload is invalid: {type(exc).__name__}",
                gate.details,
            )
        stock_by_code = {stock.code: stock for _, stock in rows}
        mismatch = any(
            feature.stock_code != stock.code or feature.stock_name != stock.name
            for feature, (_, stock) in zip(features, rows, strict=True)
        )
        if mismatch or len(stock_by_code) != len(features):
            return _JobResult.failure(
                "feature_gate",
                "STOCK_MASTER_MISMATCH",
                "Stored V9 features do not match the bound stock master rows",
                gate.details,
            )
        try:
            available_on = date.fromisoformat(str(gate.details["evaluation_entry_date"]))
            classifications = await SqlClassificationRepository(self.session).load_primary_as_of(
                available_on=available_on,
                published_by=snapshot.as_of.astimezone(SHANGHAI).date(),
                fetched_by=snapshot.locked_at,
                stock_ids=tuple(stock.id for _, stock in rows),
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _JobResult.failure(
                "classification_gate",
                "CLASSIFICATION_SET_INVALID",
                f"The effective primary classification set is invalid: {type(exc).__name__}",
                gate.details,
            )
        classification_errors = validate_feature_classifications(
            features,
            classifications,
        )
        if classification_errors:
            return _JobResult.failure(
                "classification_gate",
                "CLASSIFICATION_SET_INVALID",
                "Stored V9 features do not match effective primary classifications",
                gate.details
                | {
                    "classification_error_count": len(classification_errors),
                    "classification_errors": list(classification_errors[:20]),
                },
            )
        frozen = FrozenSnapshot(
            cutoff=snapshot.as_of,
            locked_at=snapshot.locked_at,
            content_hash=snapshot.content_hash,
            records=(),
        )
        overheat_ratio = (
            sum(feature.return_20d > 0.40 for feature in features) / len(features)
            if features
            else 0.0
        )
        rule_result = run_v9_rules(
            snapshot=frozen,
            features=features,
            market_state_input=state_input,
            candidate_overheat_ratio=overheat_ratio,
        )
        await _report(progress, "result_persistence", 90, "正在保存候选审计与决策版本。")
        conflict = await self._prepare_week(week_id, snapshot, rule_result)
        if conflict is not None:
            return conflict
        await self._persist_candidates(week_id, snapshot.id, rule_result, stock_by_code)
        if not rule_result.baseline.items:
            return _JobResult.failure(
                "rule_gate",
                "NO_ELIGIBLE_CANDIDATE",
                "V9 rules produced no eligible candidate; no decision set was created",
                gate.details | {"flags": list(rule_result.flags)},
            )
        existing = await self.session.scalar(
            select(models.DecisionSet).where(
                models.DecisionSet.week_id == week_id,
                models.DecisionSet.type == "rule",
                models.DecisionSet.is_active.is_(True),
            )
        )
        if existing is not None:
            if existing.fingerprint != rule_result.fingerprint:
                return _JobResult.failure(
                    "rule_gate",
                    "RULE_RESULT_CONFLICT",
                    "An active rule baseline already exists with another fingerprint",
                    gate.details,
                )
            return _JobResult.completed(
                gate.details,
                existing.id,
                rule_result,
                reused=True,
            )
        decision = await self._persist_rule_decision(
            week_id,
            rule_result,
            stock_by_code,
            now,
        )
        return _JobResult.completed(gate.details, decision.id, rule_result, reused=False)

    async def _prepare_week(
        self,
        week_id: date,
        snapshot: models.DataSnapshot,
        rule_result: RuleRunResult,
    ) -> "_JobResult | None":
        week = await self.session.get(models.Week, week_id)
        if week is not None and week.snapshot_id != snapshot.id:
            return _JobResult.failure(
                "rule_gate",
                "WEEK_SNAPSHOT_CONFLICT",
                "The week is already bound to another frozen snapshot",
                {"snapshot_id": str(snapshot.id)},
            )
        if week is None:
            week = models.Week(
                week_id=week_id,
                status="awaiting_approval",
                market_state=rule_result.market_state.value,
                snapshot_id=snapshot.id,
                rule_version=RULE_VERSION,
            )
            self.session.add(week)
        else:
            week.status = "awaiting_approval"
            week.market_state = rule_result.market_state.value
            week.rule_version = RULE_VERSION
        await self.session.flush()
        return None

    async def _persist_candidates(
        self,
        week_id: date,
        snapshot_id: uuid.UUID,
        rule_result: RuleRunResult,
        stock_by_code: dict[str, models.Stock],
    ) -> None:
        existing_count = await self.session.scalar(
            select(func.count(models.Candidate.id)).where(models.Candidate.week_id == week_id)
        )
        if existing_count:
            return
        eligible_rank = 0
        for candidate in rule_result.candidates:
            rank: int | None = None
            if candidate.bucket is CandidateBucket.ELIGIBLE:
                eligible_rank += 1
                rank = eligible_rank
            stock = stock_by_code[candidate.features.stock_code]
            self.session.add(
                models.Candidate(
                    id=uuid.uuid4(),
                    week_id=week_id,
                    snapshot_id=snapshot_id,
                    stock_id=stock.id,
                    rule_score=Decimal(str(candidate.rule_score)),
                    rank=rank,
                    bucket=candidate.bucket.value,
                    exclusion_reasons=list(candidate.exclusion_reasons),
                    score_breakdown=candidate.score_breakdown,
                )
            )
        await self.session.flush()

    async def _persist_rule_decision(
        self,
        week_id: date,
        rule_result: RuleRunResult,
        stock_by_code: dict[str, models.Stock],
        now: datetime,
    ) -> models.DecisionSet:
        maximum_version = await self.session.scalar(
            select(func.max(models.DecisionSet.version)).where(
                models.DecisionSet.week_id == week_id,
                models.DecisionSet.type == "rule",
            )
        )
        decision = models.DecisionSet(
            id=uuid.uuid4(),
            source_decision_set_id=None,
            week_id=week_id,
            type="rule",
            version=(maximum_version or 0) + 1,
            status="awaiting_approval",
            fingerprint=rule_result.fingerprint,
            shortage=rule_result.baseline.shortage,
            shortage_reason=rule_result.baseline.shortage_reason,
            is_active=True,
            created_at=now,
            published_at=None,
        )
        self.session.add(decision)
        await self.session.flush()
        confidence = "low" if rule_result.baseline.low_confidence else "medium"
        for rank, candidate in enumerate(rule_result.baseline.items, start=1):
            stock = stock_by_code[candidate.features.stock_code]
            self.session.add(
                models.DecisionItem(
                    id=uuid.uuid4(),
                    decision_set_id=decision.id,
                    stock_id=stock.id,
                    rank=rank,
                    role=_selection_role(candidate),
                    target_return=Decimal("0.10"),
                    confidence=confidence,
                    summary=_rule_summary(candidate),
                    primary_risk=_primary_risk(candidate, rule_result),
                )
            )
        await self.session.flush()
        return decision


ProgressCallback = Callable[[str, int, str], Awaitable[None]]


async def execute_next_queued_weekly_selection(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> JobResponse | None:
    async with session_factory() as session:
        job_id = await session.scalar(
            select(models.Job.id)
            .where(
                models.Job.job_type == "weekly_selection",
                models.Job.status == "queued",
            )
            .order_by(models.Job.created_at)
            .limit(1)
        )
    if job_id is None:
        return None
    return await execute_queued_weekly_selection(
        uuid.UUID(str(job_id)),
        session_factory,
        now=now,
    )


async def execute_queued_weekly_selection(
    job_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> JobResponse:
    execution_now = now or datetime.now(UTC)
    async with session_factory() as session, session.begin():
        job = await session.scalar(
            select(models.Job).where(models.Job.id == job_id).with_for_update()
        )
        if job is None:
            raise LookupError("weekly selection job does not exist")
        if job.status != "queued":
            return _response(job)
        if job.cancel_requested_at is not None:
            job.status = "cancelled"
            job.stage = "cancelled"
            job.finished_at = datetime.now(UTC)
            return _response(job)
        job.status = "running"
        job.stage = "calendar_gate"
        job.started_at = datetime.now(UTC)
        job.details = _append_progress(
            job.details,
            5,
            "calendar_gate",
            job.started_at,
            "后台执行器已领取任务。",
        )
        week_id = job.week_id
        assert week_id is not None

    async def progress(stage: str, percent: int, message: str) -> None:
        await _update_job_progress(
            session_factory,
            job_id,
            stage=stage,
            percent=percent,
            message=message,
        )

    try:
        async with session_factory() as work_session, work_session.begin():
            calendar_rows = list(
                await work_session.scalars(
                    select(models.TradingCalendar)
                    .where(
                        models.TradingCalendar.trade_date >= week_id,
                        models.TradingCalendar.trade_date <= week_id + timedelta(days=4),
                    )
                    .order_by(models.TradingCalendar.trade_date)
                )
            )
            formal_end = await _next_trading_week_start(work_session, week_id)
            application = SqlJobApplication(work_session)
            gate = await application._evaluate_gates(
                week_id,
                calendar_rows,
                execution_now,
                progress,
                formal_end=formal_end,
            )
            result = (
                await application._execute_rule(
                    week_id,
                    gate,
                    execution_now,
                    progress,
                )
                if gate.ready
                else _JobResult.from_gate(gate)
            )
    except Exception as exc:
        result = _JobResult.failure(
            "internal_error",
            "UNEXPECTED_JOB_FAILURE",
            f"Weekly selection failed safely: {type(exc).__name__}",
            {},
        )

    finished_at = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        job = await session.scalar(
            select(models.Job).where(models.Job.id == job_id).with_for_update()
        )
        if job is None:
            raise LookupError("weekly selection job disappeared")
        cancelled = job.cancel_requested_at is not None
        job.status = "cancelled" if cancelled else result.status
        job.stage = "cancelled" if cancelled else result.stage
        job.error_code = "JOB_CANCELLED" if cancelled else result.error_code
        job.error_message = (
            "Job stopped after the current atomic step" if cancelled else result.error_message
        )
        job.finished_at = finished_at
        message = (
            "已复用本周现有规则结果，未创建重复版本。"
            if result.status == "succeeded" and result.details.get("reused") is True
            else "规则结果已保存，可在决策版本中继续审批或查看。"
            if result.status == "succeeded"
            else "任务已安全停止，未创建不合规名单。"
        )
        job.details = _append_progress(
            job.details | result.details,
            100,
            job.stage,
            finished_at,
            "任务已在当前原子步骤完成后安全停止。" if cancelled else message,
        )
        await session.flush()
        return _response(job)


async def _update_job_progress(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
    *,
    stage: str,
    percent: int,
    message: str,
) -> None:
    async with session_factory() as session, session.begin():
        job = await session.scalar(
            select(models.Job).where(models.Job.id == job_id).with_for_update()
        )
        if job is None or job.status != "running":
            return
        job.stage = stage
        job.details = _append_progress(
            job.details,
            percent,
            stage,
            datetime.now(UTC),
            message,
        )


async def _report(
    progress: ProgressCallback | None,
    stage: str,
    percent: int,
    message: str,
) -> None:
    if progress is not None:
        await progress(stage, percent, message)


def _progress_details(
    percent: int,
    stage: str,
    at: datetime,
    message: str,
) -> dict[str, object]:
    return {
        "progress_percent": percent,
        "events": [
            {
                "stage": stage,
                "percent": percent,
                "at": at.isoformat(),
                "message": message,
            }
        ],
    }


def _append_progress(
    details: Mapping[str, object] | None,
    percent: int,
    stage: str,
    at: datetime,
    message: str,
) -> dict[str, object]:
    current = dict(details or {})
    stored_events = current.get("events")
    events = list(stored_events) if isinstance(stored_events, list) else []
    if not events or not isinstance(events[-1], dict) or events[-1].get("stage") != stage:
        events.append(
            {
                "stage": stage,
                "percent": percent,
                "at": at.isoformat(),
                "message": message,
            }
        )
    current["progress_percent"] = percent
    current["events"] = events
    return current


@dataclass(frozen=True, slots=True)
class _GateOutcome:
    stage: str
    code: str | None
    message: str | None
    details: dict[str, object]
    ready: bool = False
    snapshot: models.DataSnapshot | None = None

    @classmethod
    def failed(
        cls,
        stage: str,
        code: str,
        message: str,
        details: Mapping[str, object],
    ) -> "_GateOutcome":
        return cls(stage, code, message, dict(details))

    @classmethod
    def ready_for_rule(
        cls,
        snapshot: models.DataSnapshot,
        details: Mapping[str, object],
    ) -> "_GateOutcome":
        return cls("rule_gate", None, None, dict(details), True, snapshot)


@dataclass(frozen=True, slots=True)
class _JobResult:
    status: str
    stage: str
    error_code: str | None
    error_message: str | None
    details: dict[str, object]

    @classmethod
    def from_gate(cls, gate: _GateOutcome) -> "_JobResult":
        assert gate.code is not None and gate.message is not None
        return cls.failure(gate.stage, gate.code, gate.message, gate.details)

    @classmethod
    def failure(
        cls,
        stage: str,
        code: str,
        message: str,
        details: Mapping[str, object],
    ) -> "_JobResult":
        return cls(
            "failed",
            stage,
            code,
            message,
            dict(details) | {"formal_decision_created": False},
        )

    @classmethod
    def completed(
        cls,
        details: Mapping[str, object],
        decision_set_id: uuid.UUID,
        rule_result: RuleRunResult,
        *,
        reused: bool,
    ) -> "_JobResult":
        return cls(
            "succeeded",
            "decision_ready",
            None,
            None,
            dict(details)
            | {
                "formal_decision_created": not reused,
                "decision_set_id": str(decision_set_id),
                "candidate_count": len(rule_result.candidates),
                "baseline_count": len(rule_result.baseline.items),
                "flags": list(rule_result.flags),
                "reused": reused,
            },
        )


def _selection_role(candidate: ScoredCandidate) -> str:
    if candidate.features.primary_domain is Domain.EXTERNAL:
        return "exploration"
    if candidate.features.primary_domain is Domain.SUPPLEMENTARY:
        return "supplementary"
    if candidate.features.strong_reserve_promotion:
        return "repair"
    return "core"


def validate_feature_classifications(
    features: list[RuleFeatures],
    classifications: Mapping[str, StoredPrimaryClassification],
) -> tuple[str, ...]:
    errors: list[str] = []
    for feature in features:
        classification = classifications.get(feature.stock_code)
        if classification is None:
            errors.append(f"{feature.stock_code}:MISSING_EFFECTIVE_PRIMARY")
            continue
        if (
            feature.primary_domain is not classification.domain
            or feature.primary_sector != classification.sector_code
        ):
            errors.append(f"{feature.stock_code}:DOMAIN_OR_SECTOR_MISMATCH")
    return tuple(errors)


def _rule_summary(candidate: ScoredCandidate) -> str:
    return (
        f"V9规则基线入选；规则分{candidate.rule_score:.1f}，"
        f"方向为{candidate.features.primary_sector}。"
    )


def _primary_risk(candidate: ScoredCandidate, result: RuleRunResult) -> str:
    features = candidate.features
    if features.data_quality is not DataQuality.VERIFIED:
        return "数据尚未达到双源验证，需重点核对来源一致性。"
    if features.volatility_percentile > 0.95:
        return "波动率处于同池高位，约10%仅为研究情景。"
    if result.baseline.low_confidence:
        return "组合处于低置信度或容量不足状态，需人工复核。"
    return "约10%仅为研究情景，需关注市场状态与板块扩散变化。"


def _response(job: models.Job) -> JobResponse:
    assert job.week_id is not None
    stored_progress = job.details.get("progress_percent")
    progress_percent = stored_progress if isinstance(stored_progress, int) else 100
    return JobResponse(
        id=str(job.id),
        job_type=cast(
            Literal["weekly_selection", "daily_brief", "weekly_review", "replay"],
            job.job_type,
        ),
        week_id=job.week_id,
        mode=cast(Literal["formal", "replay"], job.mode or "formal"),
        replay_stage=job.replay_stage,
        trade_date=job.trade_date,
        replay_run_id=str(job.replay_run_id) if job.replay_run_id is not None else None,
        status=job.status,
        stage=job.stage,
        error_code=job.error_code,
        error_message=job.error_message,
        progress_percent=progress_percent,
        cancel_requested_at=job.cancel_requested_at,
        details=job.details,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )
