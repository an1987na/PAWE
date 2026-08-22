import asyncio
import uuid
from collections.abc import Awaitable, Callable, Collection, Coroutine, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler
from pawe_api.briefs.repository import SqlBriefApplication
from pawe_api.config import Settings, get_settings
from pawe_api.contracts import (
    DailyBrief,
    JobResponse,
    WeeklyReviewResponse,
    WeeklySelectionJobRequest,
)
from pawe_api.data.baseline import FEATURE_SCHEMA_VERSION, STATE_INPUT_SCHEMA_VERSION
from pawe_api.data.classification_repository import SqlClassificationRepository
from pawe_api.data.providers import (
    DailyProviderError,
    EastmoneyDailyProvider,
    ProviderPolicy,
    TencentDailyProvider,
)
from pawe_api.db import models
from pawe_api.db.session import SessionFactory, engine
from pawe_api.evaluation.formal import generate_formal_weekly_reviews
from pawe_api.jobs.repository import SqlJobApplication, execute_queued_weekly_selection
from pawe_api.replay_stage.calculation import (
    StagedReplayCalculationError,
    calculate_daily_brief,
    calculate_weekly_review,
    calculate_weekly_selection,
    persist_daily_brief,
    persist_selection,
    persist_weekly_review,
)
from pawe_api.replay_stage.repair import ReplayDataRepairService
from pawe_api.replay_stage.windows import ReplayStage
from pawe_api.watchlist.repository import (
    generate_watchlist_daily_items,
    generate_watchlist_weekly_items,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from scripts.ingest_daily_bars import ingest as ingest_daily_bars
from scripts.ingest_exchange_calendar import (
    SSE_2026_URL,
    SZSE_2026_URL,
)
from scripts.ingest_exchange_calendar import (
    ingest as ingest_exchange_calendar,
)
from scripts.materialize_technical_snapshot import materialize as materialize_snapshot
from scripts.materialize_v9_inputs import materialize as materialize_v9_inputs

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _run_async[T](coroutine: Coroutine[Any, Any, T]) -> T:
    async def execute() -> T:
        try:
            return await coroutine
        finally:
            await engine.dispose()

    return asyncio.run(execute())


def run_weekly_preopen() -> None:
    """Create or reuse this week's audited rule baseline before the first open."""
    result = _run_async(execute_weekly_preopen())
    if result is None:
        print("weekly_selection skipped: no active administrator")
        return
    print(
        f"weekly_selection week={result.week_id.isoformat()} status={result.status} "
        f"stage={result.stage} error_code={result.error_code or 'none'}"
    )


def run_weekly_data_preparation() -> None:
    """Refresh the next week's formal snapshot and conservative V9 inputs."""
    try:
        snapshot_id = _run_async(execute_weekly_data_preparation())
    except Exception as exc:
        print(f"weekly_data_preparation failed: {type(exc).__name__}: {exc}")
        return
    print(f"weekly_data_preparation snapshot_id={snapshot_id}")


def run_queued_weekly_selection() -> None:
    """Claim one persisted manual job and update its progress."""
    result = _run_async(execute_next_queued_weekly_selection_with_preparation())
    if result is not None:
        print(
            f"queued_weekly_selection week={result.week_id.isoformat()} "
            f"status={result.status} stage={result.stage}"
        )
        return
    output = _run_async(execute_next_queued_output_job())
    if output is not None:
        print(
            f"queued_output type={output.job_type} week={output.week_id.isoformat()} "
            f"status={output.status} stage={output.stage}"
        )


async def execute_weekly_data_preparation(
    *,
    now: datetime | None = None,
    week_id: date | None = None,
) -> str:
    local_now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    target_week = week_id or upcoming_week_id(local_now.date())
    if target_week.year != 2026:
        raise RuntimeError("annual exchange-calendar sources must be configured for the new year")
    calendar = await _load_week_calendar(target_week)
    if len(calendar) != 5:
        await ingest_exchange_calendar(
            target_week,
            target_week.year,
            SSE_2026_URL,
            SZSE_2026_URL,
        )
        calendar = await _load_week_calendar(target_week)
    if len(calendar) != 5:
        raise RuntimeError("weekly calendar is incomplete after refresh")
    open_days = [row for row in calendar if row.is_open]
    if len(open_days) < 3:
        raise RuntimeError("natural week has fewer than three trading days")
    previous_open = open_days[0].previous_open_date
    if previous_open is None:
        raise RuntimeError("previous open date is unavailable")
    decision_cutoff = datetime.combine(previous_open, time(15), tzinfo=SHANGHAI)
    ready_snapshot_id = await _ready_snapshot_id(decision_cutoff)
    if ready_snapshot_id is not None:
        return str(ready_snapshot_id)
    await ingest_daily_bars(
        previous_open - timedelta(days=120),
        previous_open,
        codes=(),
        limit=None,
        after_code=None,
        checkpoint=None,
        checkpoint_path=None,
        v9_available_on=target_week,
        published_by=previous_open,
    )
    fetched_by = datetime.now(UTC)
    await materialize_snapshot(
        as_of=previous_open,
        decision_cutoff=decision_cutoff,
        fetched_by=fetched_by,
        available_on=target_week,
        codes=(),
        limit=None,
        persist=True,
    )
    snapshot_id = await _latest_snapshot_id(decision_cutoff)
    if snapshot_id is None:
        raise RuntimeError("technical snapshot was not persisted")
    await materialize_v9_inputs(snapshot_id, persist=True)
    return str(snapshot_id)


async def _load_week_calendar(week_id: date) -> list[models.TradingCalendar]:
    async with SessionFactory() as session:
        return list(
            await session.scalars(
                select(models.TradingCalendar)
                .where(
                    models.TradingCalendar.trade_date >= week_id,
                    models.TradingCalendar.trade_date <= week_id + timedelta(days=4),
                )
                .order_by(models.TradingCalendar.trade_date)
            )
        )


async def _ready_snapshot_id(decision_cutoff: datetime) -> uuid.UUID | None:
    snapshot_id = await _latest_snapshot_id(decision_cutoff)
    if snapshot_id is None:
        return None
    async with SessionFactory() as session:
        record_count = int(
            await session.scalar(
                select(func.count(models.DataSnapshotRecord.id)).where(
                    models.DataSnapshotRecord.snapshot_id == snapshot_id
                )
            )
            or 0
        )
        feature_count = int(
            await session.scalar(
                select(func.count(models.WeeklyFeature.id)).where(
                    models.WeeklyFeature.snapshot_id == snapshot_id,
                    models.WeeklyFeature.feature_version == FEATURE_SCHEMA_VERSION,
                )
            )
            or 0
        )
        state_input_id = await session.scalar(
            select(models.WeeklyStateInput.id).where(
                models.WeeklyStateInput.snapshot_id == snapshot_id,
                models.WeeklyStateInput.input_version == STATE_INPUT_SCHEMA_VERSION,
            )
        )
    complete = record_count > 0 and feature_count == record_count and state_input_id is not None
    return snapshot_id if complete else None


async def _latest_snapshot_id(decision_cutoff: datetime) -> uuid.UUID | None:
    async with SessionFactory() as session:
        value = await session.scalar(
            select(models.DataSnapshot.id)
            .where(models.DataSnapshot.as_of == decision_cutoff)
            .order_by(models.DataSnapshot.locked_at.desc())
            .limit(1)
        )
    return uuid.UUID(str(value)) if value is not None else None


async def execute_weekly_preopen(
    *,
    now: datetime | None = None,
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> JobResponse | None:
    local_now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    week_id = natural_week_id(local_now.date())
    async with session_factory() as session:
        actor_id = await session.scalar(
            select(models.User.id)
            .where(models.User.role == "admin", models.User.is_active.is_(True))
            .order_by(models.User.created_at, models.User.id)
            .limit(1)
        )
        if actor_id is None:
            return None
    publication_deadline = datetime.combine(week_id, time(9, 30), tzinfo=SHANGHAI)
    if local_now < publication_deadline:
        await execute_weekly_data_preparation(now=local_now, week_id=week_id)
    request = WeeklySelectionJobRequest(
        week_id=week_id,
        idempotency_key=(f"worker-weekly-{week_id.isoformat()}-{local_now.date().isoformat()}"),
    )
    async with session_factory() as session:
        return await SqlJobApplication(session).trigger_weekly_selection(
            request,
            uuid.UUID(str(actor_id)),
        )


async def execute_next_queued_weekly_selection_with_preparation(
    *,
    now: datetime | None = None,
) -> JobResponse | None:
    local_now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    async with SessionFactory() as session:
        queued = (
            await session.execute(
                select(models.Job.id, models.Job.week_id)
                .where(
                    models.Job.job_type == "weekly_selection",
                    models.Job.status == "queued",
                )
                .order_by(models.Job.created_at)
                .limit(1)
            )
        ).first()
    if queued is None:
        return None
    job_id, week_id = queued
    assert week_id is not None
    async with SessionFactory() as session:
        next_open = await session.scalar(
            select(models.TradingCalendar.trade_date)
            .where(
                models.TradingCalendar.trade_date > week_id + timedelta(days=6),
                models.TradingCalendar.is_open.is_(True),
            )
            .order_by(models.TradingCalendar.trade_date)
            .limit(1)
        )
    formal_end = datetime.combine(
        next_open or (week_id + timedelta(days=7)), time(0), tzinfo=SHANGHAI
    )
    if local_now < formal_end:
        try:
            await execute_weekly_data_preparation(now=local_now, week_id=week_id)
        except Exception as exc:
            print(f"weekly_selection preparation failed: {type(exc).__name__}: {exc}")
    return await execute_queued_weekly_selection(
        uuid.UUID(str(job_id)),
        SessionFactory,
        now=local_now,
    )


def natural_week_id(today: date) -> date:
    return today - timedelta(days=today.weekday())


def upcoming_week_id(today: date) -> date:
    days_until_monday = (7 - today.weekday()) % 7
    return today + timedelta(days=days_until_monday)


def run_daily_brief() -> None:
    """Refresh and persist the deterministic brief for today's published targets."""
    try:
        brief = _run_async(execute_daily_brief())
    except Exception as exc:
        print(f"daily_brief failed: {type(exc).__name__}: {exc}")
        return
    if brief is None:
        print("daily_brief skipped: market closed or no published decision")
        return
    print(
        f"daily_brief week={brief.week_id.isoformat()} "
        f"trade_date={brief.trade_date.isoformat()} items={len(brief.items)}"
    )


async def _daily_target_codes(target_date: date) -> tuple[tuple[str, ...], tuple[str, ...]]:
    week_id = natural_week_id(target_date)
    async with SessionFactory() as session:
        published_codes = tuple(
            (
                await session.execute(
                    select(models.Stock.code)
                    .join(models.DecisionItem, models.DecisionItem.stock_id == models.Stock.id)
                    .join(
                        models.DecisionSet,
                        models.DecisionSet.id == models.DecisionItem.decision_set_id,
                    )
                    .where(
                        models.DecisionSet.week_id == week_id,
                        models.DecisionSet.type == "published",
                        models.DecisionSet.status == "published",
                        models.DecisionSet.is_active.is_(True),
                    )
                    .order_by(models.DecisionItem.rank)
                )
            ).scalars()
        )
        watchlist_codes = tuple(
            await session.scalars(
                select(models.Stock.code)
                .join(
                    models.UserWatchlistItem,
                    models.UserWatchlistItem.stock_id == models.Stock.id,
                )
                .where(
                    models.UserWatchlistItem.effective_from <= target_date,
                    (
                        models.UserWatchlistItem.removed_at.is_(None)
                        | (
                            models.UserWatchlistItem.removed_at
                            > datetime.combine(
                                target_date, time(15, 30), tzinfo=SHANGHAI
                            ).astimezone(UTC)
                        )
                    ),
                )
                .distinct()
            )
        )
    return published_codes, watchlist_codes


async def execute_daily_data_refresh(
    *,
    now: datetime | None = None,
    trade_date: date | None = None,
) -> int:
    """Fetch the current daily window as the first stage of the brief task."""
    local_now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    target_date = trade_date or local_now.date()
    async with SessionFactory() as session:
        calendar = await session.get(models.TradingCalendar, target_date)
    if calendar is None or not calendar.is_open:
        return 0
    published_codes, watchlist_codes = await _daily_target_codes(target_date)
    all_codes = tuple(sorted(set(published_codes) | set(watchlist_codes)))
    if not all_codes:
        return 0
    await ingest_daily_bars(
        target_date - timedelta(days=120),
        target_date,
        codes=all_codes,
        limit=None,
        after_code=None,
        checkpoint=None,
        checkpoint_path=None,
        v9_available_on=None,
        published_by=None,
        allow_sina_fallback=False,
        provider_timeout_seconds=5,
        provider_retry_count=0,
    )
    return len(all_codes)


async def execute_daily_brief(
    *,
    now: datetime | None = None,
    trade_date: date | None = None,
) -> DailyBrief | None:
    local_now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    target_date = trade_date or local_now.date()
    async with SessionFactory() as session:
        calendar = await session.get(models.TradingCalendar, target_date)
        if calendar is None or not calendar.is_open:
            return None
    rows, watch_rows = await _daily_target_codes(target_date)
    all_codes = tuple(sorted(set(rows) | set(watch_rows)))
    if not all_codes:
        return None
    # Keep an in-task refresh as a safe fallback when the proactive refresh
    # was missed or one of its providers was temporarily unavailable.
    await execute_daily_data_refresh(now=local_now, trade_date=target_date)
    fetched_at = datetime.now(UTC)
    public_brief = None
    if rows:
        async with SessionFactory() as session:
            public_brief = await SqlBriefApplication(session).generate(
                target_date,
                fetched_at=fetched_at,
            )
    async with SessionFactory() as session:
        await generate_watchlist_daily_items(session, target_date, fetched_at=fetched_at)
    if public_brief is not None:
        await execute_weekly_review_after_daily_brief(
            now=local_now,
            week_id=public_brief.week_id,
        )
    return public_brief


def missing_daily_brief_targets(
    *,
    target_week: date,
    published_decisions: Mapping[date, str],
    open_dates_by_week: Mapping[date, Sequence[date]],
    calendar_dates_by_week: Mapping[date, Sequence[date]],
    active_briefs: Collection[tuple[date, str, date]],
    today: date,
) -> tuple[tuple[date, date], ...]:
    """Return completed-week open dates without an active published brief.

    This pure helper keeps the catch-up policy independent from SQLAlchemy and
    makes the idempotency key explicit: one published decision and one trading
    date identify a brief that must exist.
    """
    existing = set(active_briefs)
    targets: list[tuple[date, date]] = []
    decision_id = published_decisions.get(target_week)
    calendar_dates = tuple(sorted(set(calendar_dates_by_week.get(target_week, ()))))
    open_dates = tuple(sorted(set(open_dates_by_week.get(target_week, ()))))
    if decision_id is None or len(calendar_dates) != 5 or not open_dates:
        return ()
    if open_dates[-1] >= today:
        return ()
    targets.extend(
        (target_week, trade_date)
        for trade_date in open_dates
        if (target_week, decision_id, trade_date) not in existing
    )
    return tuple(targets)


async def _missing_daily_brief_targets(*, today: date) -> tuple[tuple[date, date], ...]:
    target_week = natural_week_id(today) - timedelta(days=7)
    async with SessionFactory() as session:
        decision_rows = list(
            (
                await session.execute(
                    select(models.DecisionSet.week_id, models.DecisionSet.id)
                    .where(
                        models.DecisionSet.week_id == target_week,
                        models.DecisionSet.type == "published",
                        models.DecisionSet.status == "published",
                        models.DecisionSet.is_active.is_(True),
                    )
                    .order_by(models.DecisionSet.week_id, models.DecisionSet.version.desc())
                )
            ).all()
        )
        published_decisions: dict[date, str] = {}
        for week_id, decision_id in decision_rows:
            published_decisions.setdefault(week_id, str(decision_id))

        calendar_rows = list(
            (
                await session.execute(
                    select(models.TradingCalendar.trade_date, models.TradingCalendar.is_open)
                    .where(
                        models.TradingCalendar.trade_date >= target_week,
                        models.TradingCalendar.trade_date <= target_week + timedelta(days=4),
                    )
                    .order_by(models.TradingCalendar.trade_date)
                )
            ).all()
        )
        calendar_dates_by_week = {target_week: tuple(day for day, _ in calendar_rows)}
        open_dates_by_week = {target_week: tuple(day for day, is_open in calendar_rows if is_open)}
        active_briefs = {
            (brief.week_id, str(brief.decision_set_id), brief.trade_date)
            for brief in await session.scalars(
                select(models.DailyBrief).where(
                    models.DailyBrief.week_id == target_week,
                    models.DailyBrief.is_active.is_(True),
                    models.DailyBrief.status == "published",
                )
            )
        }
    return missing_daily_brief_targets(
        target_week=target_week,
        published_decisions=published_decisions,
        open_dates_by_week=open_dates_by_week,
        calendar_dates_by_week=calendar_dates_by_week,
        active_briefs=active_briefs,
        today=today,
    )


async def execute_daily_brief_catchup(
    *, now: datetime | None = None
) -> tuple[tuple[date, date, str], ...]:
    """Generate missing historical briefs and, when due, today's brief."""
    local_now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    targets = await _missing_daily_brief_targets(today=local_now.date())
    outcomes: list[tuple[date, date, str]] = []
    for week_id, trade_date in targets:
        try:
            brief = await execute_daily_brief(now=local_now, trade_date=trade_date)
            status = "generated" if brief is not None else "skipped"
        except Exception as exc:
            status = f"failed:{type(exc).__name__}"
            print(
                f"daily_brief_catchup failed week={week_id.isoformat()} "
                f"trade_date={trade_date.isoformat()} {type(exc).__name__}: {exc}"
            )
        outcomes.append((week_id, trade_date, status))
    daily_due_at = datetime.combine(local_now.date(), time(15, 30), tzinfo=SHANGHAI)
    if local_now.weekday() < 5 and local_now >= daily_due_at:
        try:
            brief = await execute_daily_brief(now=local_now)
            if brief is not None:
                outcomes.append((natural_week_id(local_now.date()), local_now.date(), "generated"))
        except Exception as exc:
            print(
                f"daily_brief_catchup failed week={natural_week_id(local_now.date()).isoformat()} "
                f"trade_date={local_now.date().isoformat()} {type(exc).__name__}: {exc}"
            )
            outcomes.append(
                (
                    natural_week_id(local_now.date()),
                    local_now.date(),
                    f"failed:{type(exc).__name__}",
                )
            )
    return tuple(outcomes)


def run_daily_brief_catchup() -> None:
    try:
        outcomes = _run_async(execute_daily_brief_catchup())
    except Exception as exc:
        print(f"daily_brief_catchup failed: {type(exc).__name__}: {exc}")
        return
    if outcomes:
        print(f"daily_brief_catchup targets={len(outcomes)}")


def run_weekly_review() -> None:
    """Evaluate rule, AI and published versions after the final weekly close."""
    try:
        results = _run_async(execute_weekly_review())
    except Exception as exc:
        print(f"weekly_review failed: {type(exc).__name__}: {exc}")
        return
    if not results:
        print("weekly_review skipped: week is incomplete or has no decision")
        return
    print(f"weekly_review week={results[0].week_id.isoformat()} versions={len(results)}")


def run_weekly_review_catchup() -> None:
    """Recover the most recent completed week's review after worker downtime."""
    try:
        results = _run_async(execute_weekly_review_catchup())
    except Exception as exc:
        print(f"weekly_review_catchup failed: {type(exc).__name__}: {exc}")
        return
    if results:
        print(
            f"weekly_review_catchup week={results[0].week_id.isoformat()} versions={len(results)}"
        )


async def _formal_daily_briefs_complete(week_id: date) -> bool:
    async with SessionFactory() as session:
        calendar = list(
            await session.scalars(
                select(models.TradingCalendar)
                .where(
                    models.TradingCalendar.trade_date >= week_id,
                    models.TradingCalendar.trade_date <= week_id + timedelta(days=4),
                )
                .order_by(models.TradingCalendar.trade_date)
            )
        )
        open_dates = {row.trade_date for row in calendar if row.is_open}
        if len(calendar) != 5 or len(open_dates) < 3:
            return False
        published_decision_id = await session.scalar(
            select(models.DecisionSet.id)
            .where(
                models.DecisionSet.week_id == week_id,
                models.DecisionSet.type == "published",
                models.DecisionSet.status == "published",
                models.DecisionSet.is_active.is_(True),
            )
            .order_by(models.DecisionSet.version.desc())
            .limit(1)
        )
        if published_decision_id is None:
            return False
        brief_dates = set(
            await session.scalars(
                select(models.DailyBrief.trade_date).where(
                    models.DailyBrief.week_id == week_id,
                    models.DailyBrief.decision_set_id == published_decision_id,
                    models.DailyBrief.status == "published",
                    models.DailyBrief.is_active.is_(True),
                )
            )
        )
    return open_dates <= brief_dates


async def execute_weekly_review_after_daily_brief(
    *,
    now: datetime,
    week_id: date,
) -> list[WeeklyReviewResponse]:
    """Start the weekly review only after every formal daily brief exists."""
    due_at = await _weekly_review_due_at(week_id)
    if due_at is None or now < due_at:
        return []
    if not await _formal_daily_briefs_complete(week_id):
        return []
    if await _weekly_review_exists(week_id):
        return []
    try:
        return await execute_weekly_review(now=now, week_id=week_id)
    except Exception as exc:
        print(f"weekly_review_after_daily failed: {type(exc).__name__}: {exc}")
        return []


async def _weekly_review_exists(week_id: date) -> bool:
    async with SessionFactory() as session:
        review_id = await session.scalar(
            select(models.WeeklyReview.id)
            .where(
                models.WeeklyReview.week_id == week_id,
                models.WeeklyReview.is_active.is_(True),
            )
            .limit(1)
        )
    return review_id is not None


async def execute_weekly_review_catchup(
    *, now: datetime | None = None
) -> list[WeeklyReviewResponse]:
    local_now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    current_week = natural_week_id(local_now.date())
    target_week = current_week if local_now.weekday() >= 4 else current_week - timedelta(days=7)
    if local_now.weekday() == 4:
        due_at = await _weekly_review_due_at(target_week)
        if due_at is None or local_now < due_at:
            target_week -= timedelta(days=7)
    async with SessionFactory() as session:
        existing = await session.scalar(
            select(models.WeeklyReview.id)
            .where(
                models.WeeklyReview.week_id == target_week,
                models.WeeklyReview.is_active.is_(True),
            )
            .limit(1)
        )
    if existing is not None:
        return []
    return await execute_weekly_review(now=local_now, week_id=target_week)


async def execute_weekly_review(
    *,
    now: datetime | None = None,
    week_id: date | None = None,
    refresh_market_data: bool = True,
) -> list[WeeklyReviewResponse]:
    local_now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    target_week = week_id or natural_week_id(local_now.date())
    due_at = await _weekly_review_due_at(target_week)
    if due_at is None or local_now < due_at:
        return []
    if not await _formal_daily_briefs_complete(target_week):
        return []
    review_codes = await _formal_review_codes(target_week, local_now)
    if review_codes and refresh_market_data:
        missing_review_codes = await _missing_weekly_market_codes(
            review_codes,
            week_id=target_week,
        )
    else:
        missing_review_codes = ()
    if missing_review_codes:
        await ingest_daily_bars(
            target_week - timedelta(days=10),
            target_week + timedelta(days=4),
            codes=missing_review_codes,
            limit=None,
            after_code=None,
            checkpoint=None,
            checkpoint_path=None,
            v9_available_on=None,
            published_by=None,
            allow_sina_fallback=False,
            provider_timeout_seconds=5,
            provider_retry_count=1,
        )
    try:
        benchmark_return = await _benchmark_return(
            target_week,
            target_week + timedelta(days=4),
        )
    except (DailyProviderError, RuntimeError):
        benchmark_return = None
    reviews = await generate_formal_weekly_reviews(
        SessionFactory,
        target_week,
        generated_at=local_now,
        benchmark_return=benchmark_return,
    )
    async with SessionFactory() as session:
        watch_codes = tuple(
            await session.scalars(
                select(models.Stock.code)
                .join(
                    models.UserWatchlistItem,
                    models.UserWatchlistItem.stock_id == models.Stock.id,
                )
                .where(
                    models.UserWatchlistItem.effective_from <= target_week + timedelta(days=4),
                    (
                        models.UserWatchlistItem.removed_at.is_(None)
                        | (
                            models.UserWatchlistItem.removed_at
                            > datetime.combine(
                                target_week, time(15, 30), tzinfo=SHANGHAI
                            ).astimezone(UTC)
                        )
                    ),
                )
                .distinct()
            )
        )
    if watch_codes:
        missing_watch_codes = (
            await _missing_weekly_market_codes(watch_codes, week_id=target_week)
            if refresh_market_data
            else ()
        )
        if missing_watch_codes:
            await ingest_daily_bars(
                target_week - timedelta(days=10),
                target_week + timedelta(days=4),
                codes=missing_watch_codes,
                limit=None,
                after_code=None,
                checkpoint=None,
                checkpoint_path=None,
                v9_available_on=None,
                published_by=None,
                allow_sina_fallback=False,
                provider_timeout_seconds=5,
                provider_retry_count=0,
            )
        async with SessionFactory() as session:
            await generate_watchlist_weekly_items(session, target_week, generated_at=local_now)
    return reviews


async def _missing_weekly_market_codes(
    codes: Sequence[str],
    *,
    week_id: date,
) -> tuple[str, ...]:
    if not codes:
        return ()
    async with SessionFactory() as session:
        open_dates = tuple(
            await session.scalars(
                select(models.TradingCalendar.trade_date)
                .where(
                    models.TradingCalendar.trade_date >= week_id,
                    models.TradingCalendar.trade_date <= week_id + timedelta(days=4),
                    models.TradingCalendar.is_open.is_(True),
                )
                .order_by(models.TradingCalendar.trade_date)
            )
        )
        if not open_dates:
            return tuple(codes)
        rows = (
            await session.execute(
                select(
                    models.Stock.code,
                    func.count(func.distinct(models.DailyBar.trade_date)),
                )
                .join(models.DailyBar, models.DailyBar.stock_id == models.Stock.id)
                .where(
                    models.Stock.code.in_(codes),
                    models.DailyBar.trade_date.in_(open_dates),
                )
                .group_by(models.Stock.code)
            )
        ).all()
    coverage = {code: int(count) for code, count in rows}
    return tuple(code for code in codes if coverage.get(code, 0) < len(open_dates))


async def _formal_review_codes(week_id: date, generated_at: datetime) -> tuple[str, ...]:
    async with SessionFactory() as session:
        selected = list(
            (
                await session.execute(
                    select(models.Stock.id, models.Stock.code)
                    .join(
                        models.DecisionItem,
                        models.DecisionItem.stock_id == models.Stock.id,
                    )
                    .join(
                        models.DecisionSet,
                        models.DecisionSet.id == models.DecisionItem.decision_set_id,
                    )
                    .where(
                        models.DecisionSet.week_id == week_id,
                        models.DecisionSet.type.in_(("rule", "ai", "published")),
                    )
                    .distinct()
                )
            ).all()
        )
        if not selected:
            return ()
        classifications = await SqlClassificationRepository(session).load_primary_as_of(
            available_on=week_id,
            published_by=week_id + timedelta(days=4),
            fetched_by=generated_at,
        )
        selected_sectors = {
            classifications[code].sector_code for _, code in selected if code in classifications
        }
        peer_ids = {
            classification.stock_id
            for classification in classifications.values()
            if classification.sector_code in selected_sectors
        }
        codes = list(
            await session.scalars(
                select(models.Stock.code)
                .where(models.Stock.status == "active", models.Stock.id.in_(peer_ids))
                .order_by(models.Stock.code)
            )
        )
    return tuple(codes)


async def execute_next_queued_output_job(
    *,
    now: datetime | None = None,
) -> JobResponse | None:
    local_now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    async with SessionFactory() as session:
        replay_job = await SqlJobApplication(session).claim_next_replay_job()
    if replay_job is not None:
        return await execute_replay_job(uuid.UUID(replay_job.id), now=local_now)
    async with SessionFactory() as session:
        application = SqlJobApplication(session)
        claimed = await application.claim_next_output_job()
    if claimed is None:
        return None
    job_id = uuid.UUID(claimed.id)
    try:
        if claimed.job_type == "daily_brief":
            raw_trade_date = claimed.details.get("trade_date")
            if not isinstance(raw_trade_date, str):
                raise ValueError("daily brief job is missing trade_date")
            target_date = date.fromisoformat(raw_trade_date)
            due_at = datetime.combine(target_date, time(15, 30), tzinfo=SHANGHAI)
            if target_date > local_now.date() or local_now < due_at:
                return await _finish_output_failure(
                    job_id,
                    "daily_gate",
                    "OUTPUT_NOT_READY",
                    "日报只能在目标交易日 15:30 后人工生成。",
                )
            await _update_output_progress(
                job_id,
                "daily_data_fetch",
                45,
                "正在抓取目标交易日收盘行情并核对数据质量。",
            )
            brief = await execute_daily_brief(now=local_now, trade_date=target_date)
            if brief is None:
                return await _finish_output_failure(
                    job_id,
                    "daily_gate",
                    "OUTPUT_NOT_AVAILABLE",
                    "目标日休市、没有正式发布名单或缺少可用收盘数据。",
                )
            return await _finish_output_success(
                job_id,
                "daily_brief_ready",
                f"{target_date.isoformat()} 日报已生成，共 {len(brief.items)} 只标的。",
                {
                    "trade_date": target_date.isoformat(),
                    "output_count": len(brief.items),
                    "quality": brief.quality.value,
                },
            )
        if claimed.job_type == "weekly_review":
            review_due_at = await _weekly_review_due_at(claimed.week_id)
            if review_due_at is None:
                return await _finish_output_failure(
                    job_id,
                    "review_gate",
                    "OUTPUT_NOT_AVAILABLE",
                    "本周交易日历不完整或少于 3 个交易日。",
                )
            if local_now < review_due_at:
                return await _finish_output_failure(
                    job_id,
                    "review_gate",
                    "OUTPUT_NOT_READY",
                    "周终复盘只能在本周最后交易日收盘后生成。",
                )
            await _update_output_progress(
                job_id,
                "review_data_fetch",
                45,
                "正在补齐正式标的与行业基准的周内行情。",
            )
            reviews = await execute_weekly_review(
                now=local_now,
                week_id=claimed.week_id,
            )
            if not reviews:
                return await _finish_output_failure(
                    job_id,
                    "review_gate",
                    "OUTPUT_NOT_AVAILABLE",
                    "本周尚不满足完整交易周、决策版本或数据完整性要求。",
                )
            return await _finish_output_success(
                job_id,
                "weekly_review_ready",
                f"{claimed.week_id.isoformat()} 周终复盘已生成，共 {len(reviews)} 个决策版本。",
                {"output_count": len(reviews)},
            )
        raise ValueError(f"unsupported output job type: {claimed.job_type}")
    except Exception as exc:
        return await _finish_output_failure(
            job_id,
            "output_error",
            "OUTPUT_GENERATION_FAILED",
            f"产出任务已安全停止：{type(exc).__name__}。",
        )


async def execute_replay_job(
    job_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> JobResponse:
    """Run isolated replay stages without touching formal decision/output tables."""
    local_now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    async with SessionFactory() as session, session.begin():
        job = await session.scalar(
            select(models.Job).where(models.Job.id == job_id).with_for_update()
        )
        if job is None or job.replay_run_id is None:
            raise LookupError("replay job or replay run does not exist")
        replay = await session.scalar(
            select(models.ReplayRun)
            .where(models.ReplayRun.id == job.replay_run_id)
            .with_for_update()
        )
        if replay is None:
            raise LookupError("replay run does not exist")
        stages = list(
            await session.scalars(
                select(models.ReplayStageRun)
                .where(models.ReplayStageRun.replay_run_id == replay.id)
                .order_by(models.ReplayStageRun.created_at, models.ReplayStageRun.trade_date)
                .with_for_update()
            )
        )
        stage_order = {
            ReplayStage.WEEKLY_SELECTION: 0,
            ReplayStage.DAILY_BRIEF: 1,
            ReplayStage.WEEKLY_REVIEW: 2,
        }
        stages.sort(
            key=lambda item: (
                stage_order[ReplayStage(item.stage)],
                item.trade_date or date.min,
            )
        )
        replay.status = "running"
        replay.updated_at = local_now
        failed = False
        for stage in stages:
            if stage.status != "queued":
                continue
            stage_kind = ReplayStage(stage.stage)
            required_dependencies = {ReplayStage.WEEKLY_SELECTION}
            if stage_kind is ReplayStage.WEEKLY_SELECTION:
                required_dependencies = set()
            elif stage_kind is ReplayStage.WEEKLY_REVIEW:
                required_dependencies.add(ReplayStage.DAILY_BRIEF)
            dependency_failed = any(
                previous.status in {"failed", "skipped"}
                and ReplayStage(previous.stage) in required_dependencies
                for previous in stages
                if previous.id != stage.id
            )
            if dependency_failed:
                stage.status = "skipped"
                stage.error_code = "REPLAY_DEPENDENCY_FAILED"
                stage.error_message = "依赖阶段失败，未生成回溯产出。"
                stage.finished_at = local_now
                failed = True
                continue
            stage.status = "running"
            stage.started_at = local_now
            stage.actual_run_at = local_now
            stage.details = stage.details | {"execution_started_at": local_now.isoformat()}
            try:
                await _materialize_replay_stage(
                    session,
                    replay,
                    stage,
                    local_now,
                )
                stage.status = "succeeded"
                stage.finished_at = local_now
            except StagedReplayCalculationError as exc:
                stage.status = "failed"
                stage.error_code = exc.code
                stage.error_message = str(exc)
                stage.warnings = list(
                    dict.fromkeys((*stage.warnings, *exc.warnings, "REPLAY_STAGE_FAILURE_ISOLATED"))
                )
                stage.details = stage.details | {"coverage": exc.coverage}
                stage.finished_at = local_now
                failed = True
            except Exception as exc:
                stage.status = "failed"
                stage.error_code = "REPLAY_STAGE_FAILED"
                stage.error_message = f"回溯阶段已安全停止：{type(exc).__name__}。"
                stage.warnings = list(stage.warnings) + ["REPLAY_STAGE_FAILURE_ISOLATED"]
                stage.finished_at = local_now
                failed = True
        replay.status = "failed" if failed else "succeeded"
        replay.updated_at = local_now
        job.status = "failed" if failed else "succeeded"
        job.stage = "replay_failed" if failed else "replay_ready"
        job.finished_at = local_now
        job.error_code = "REPLAY_STAGE_FAILED" if failed else None
        job.error_message = "部分回溯阶段失败或被跳过。" if failed else None
        job.details = job.details | {
            "progress_percent": 100,
            "stage_count": len(stages),
            "completed_stage_count": sum(stage.status == "succeeded" for stage in stages),
            "isolated_formal_tables": True,
        }
        return _job_response(job)


async def _materialize_replay_stage(
    session: AsyncSession,
    replay: models.ReplayRun,
    stage: models.ReplayStageRun,
    actual_run_at: datetime,
    *,
    repair_service: ReplayDataRepairService | None = None,
) -> None:
    """Calculate and persist one replay-only stage without formal writes."""
    stage.details = stage.details | {
        "information_cutoff": stage.information_cutoff.isoformat(),
        "actual_run_at": actual_run_at.isoformat(),
        "formal_tables_written": False,
    }
    repair_service = repair_service or ReplayDataRepairService(
        max_codes=400 if stage.stage == ReplayStage.WEEKLY_SELECTION.value else 48
    )
    if stage.stage == ReplayStage.WEEKLY_SELECTION.value:
        selection_calculation = await _calculate_with_repair(
            stage,
            actual_run_at,
            repair_service,
            lambda: calculate_weekly_selection(session, replay, actual_run_at=actual_run_at),
        )
        await persist_selection(
            session, replay, stage, selection_calculation, actual_run_at=actual_run_at
        )
        return
    if stage.stage == ReplayStage.DAILY_BRIEF.value:
        daily_calculation = await _calculate_with_repair(
            stage,
            actual_run_at,
            repair_service,
            lambda: calculate_daily_brief(session, replay, stage, actual_run_at=actual_run_at),
        )
        await persist_daily_brief(
            session, replay, stage, daily_calculation, actual_run_at=actual_run_at
        )
        return
    if stage.stage == ReplayStage.WEEKLY_REVIEW.value:
        review_calculation = await _calculate_with_repair(
            stage,
            actual_run_at,
            repair_service,
            lambda: calculate_weekly_review(session, replay, stage, actual_run_at=actual_run_at),
        )
        await persist_weekly_review(
            session, replay, stage, review_calculation, actual_run_at=actual_run_at
        )
        return
    raise StagedReplayCalculationError(
        f"unsupported replay stage: {stage.stage}",
        code="REPLAY_STAGE_UNSUPPORTED",
    )


async def _calculate_with_repair(
    stage: models.ReplayStageRun,
    actual_run_at: datetime,
    repair_service: ReplayDataRepairService,
    calculate: Callable[[], Awaitable[Any]],
) -> Any:
    """Retry one failed stage once after a bounded point-in-time repair."""
    try:
        return await calculate()
    except StagedReplayCalculationError as original_error:
        raw_codes = original_error.coverage.get("missing_codes")
        missing_codes = (
            tuple(sorted({code for code in raw_codes if isinstance(code, str)}))
            if isinstance(raw_codes, (list, tuple, set))
            else ()
        )
        attempts = stage.details.get("repair_attempts", 0)
        repair_attempts = attempts if isinstance(attempts, int) else 0
        if not missing_codes or repair_attempts >= 1:
            raise
        as_of = stage.trade_date or stage.information_cutoff.astimezone(SHANGHAI).date()
        result = await repair_service.repair(
            missing_codes,
            as_of=as_of,
            information_cutoff=stage.information_cutoff,
            attempted_at=actual_run_at,
        )
        stage.details = stage.details | {
            "repair_attempts": repair_attempts + 1,
            "data_repair": result.as_details(),
        }
        stage.warnings = list(
            dict.fromkeys((*stage.warnings, "REPLAY_DATA_REPAIR_ATTEMPTED", *result.warnings))
        )
        original_error.coverage = {
            **original_error.coverage,
            "data_repair": result.as_details(),
        }
        if result.status not in {"completed", "partial"}:
            raise
        try:
            return await calculate()
        except StagedReplayCalculationError as retry_error:
            retry_error.warnings = tuple(dict.fromkeys((*retry_error.warnings, *result.warnings)))
            retry_error.coverage = {
                **retry_error.coverage,
                "data_repair": result.as_details(),
            }
            raise


def _job_response(job: models.Job) -> JobResponse:
    stored = job.details.get("progress_percent")
    return JobResponse(
        id=str(job.id),
        job_type="replay",
        week_id=job.week_id,
        mode="replay",
        replay_stage=job.replay_stage,
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


async def _weekly_review_due_at(week_id: date) -> datetime | None:
    async with SessionFactory() as session:
        open_dates = list(
            await session.scalars(
                select(models.TradingCalendar.trade_date)
                .where(
                    models.TradingCalendar.trade_date >= week_id,
                    models.TradingCalendar.trade_date <= week_id + timedelta(days=4),
                    models.TradingCalendar.is_open.is_(True),
                )
                .order_by(models.TradingCalendar.trade_date)
            )
        )
    if len(open_dates) < 3:
        return None
    return datetime.combine(open_dates[-1], time(15, 30), tzinfo=SHANGHAI)


async def _update_output_progress(
    job_id: uuid.UUID,
    stage: str,
    percent: int,
    message: str,
) -> None:
    async with SessionFactory() as session:
        await SqlJobApplication(session).update_output_job_progress(
            job_id,
            stage=stage,
            percent=percent,
            message=message,
        )


async def _finish_output_success(
    job_id: uuid.UUID,
    stage: str,
    message: str,
    details: dict[str, object],
) -> JobResponse:
    async with SessionFactory() as session:
        return await SqlJobApplication(session).finish_output_job(
            job_id,
            succeeded=True,
            stage=stage,
            message=message,
            details=details,
        )


async def _finish_output_failure(
    job_id: uuid.UUID,
    stage: str,
    error_code: str,
    message: str,
) -> JobResponse:
    async with SessionFactory() as session:
        return await SqlJobApplication(session).finish_output_job(
            job_id,
            succeeded=False,
            stage=stage,
            message=message,
            error_code=error_code,
            error_message=message,
        )


async def _benchmark_return(start: date, end: date) -> float:
    policy = ProviderPolicy(timeout_seconds=10, retry_count=2, min_interval_seconds=0)
    async with httpx.AsyncClient(follow_redirects=True, trust_env=False) as client:
        try:
            series = await TencentDailyProvider(client, policy=policy).fetch("sh000300", start, end)
        except DailyProviderError:
            series = await EastmoneyDailyProvider(client, policy=policy).fetch(
                "sh000300", start, end
            )
    if not series.bars:
        raise RuntimeError("CSI300 benchmark bars are unavailable")
    return float(series.bars[-1].close / series.bars[0].open - 1)


def build_scheduler(settings: Settings) -> BlockingScheduler:
    scheduler = BlockingScheduler(
        timezone=ZoneInfo("Asia/Shanghai"),
        executors={"default": ThreadPoolExecutor(max_workers=1)},
    )
    scheduler.add_job(
        run_queued_weekly_selection,
        trigger="interval",
        seconds=settings.job_poll_seconds,
        id="weekly-job-runner",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_weekly_data_preparation,
        trigger="cron",
        day_of_week="sun",
        hour=settings.weekly_data_refresh_hour,
        minute=settings.weekly_data_refresh_minute,
        id="weekly-data-preparation",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_weekly_preopen,
        trigger="cron",
        day_of_week="mon-fri",
        hour=settings.weekly_preopen_hour,
        minute=settings.weekly_preopen_minute,
        id="weekly-preopen",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_daily_brief,
        trigger="cron",
        day_of_week="mon-fri",
        hour=settings.daily_brief_hour,
        minute=settings.daily_brief_minute,
        id="daily-brief",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler


def add_startup_catchups(
    scheduler: BlockingScheduler,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> None:
    """Recover idempotent preparation and output tasks after worker downtime."""
    local_now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    if local_now.weekday() < 5:
        daily_due_at = datetime.combine(
            local_now.date(),
            time(settings.daily_brief_hour, settings.daily_brief_minute),
            tzinfo=SHANGHAI,
        )
        if local_now >= daily_due_at:
            scheduler.add_job(
                run_daily_brief_catchup,
                trigger="date",
                run_date=local_now + timedelta(seconds=1),
                id="daily-brief-startup-catchup",
                replace_existing=True,
            )

    if local_now.weekday() == 6:
        preparation_due_at = datetime.combine(
            local_now.date(),
            time(settings.weekly_data_refresh_hour, settings.weekly_data_refresh_minute),
            tzinfo=SHANGHAI,
        )
        if local_now >= preparation_due_at:
            scheduler.add_job(
                run_weekly_data_preparation,
                trigger="date",
                run_date=local_now + timedelta(seconds=2),
                id="weekly-data-preparation-startup-catchup",
                replace_existing=True,
            )

    current_week = natural_week_id(local_now.date())
    publication_deadline = datetime.combine(current_week, time(9, 30), tzinfo=SHANGHAI)
    if local_now.weekday() == 0 and local_now < publication_deadline:
        scheduler.add_job(
            run_weekly_preopen,
            trigger="date",
            run_date=local_now + timedelta(seconds=3),
            id="weekly-preopen-startup-catchup",
            replace_existing=True,
        )

    scheduler.add_job(
        run_weekly_review_catchup,
        trigger="date",
        run_date=local_now + timedelta(seconds=4),
        id="weekly-review-startup-catchup",
        replace_existing=True,
    )


def main() -> None:
    settings = get_settings()
    scheduler = build_scheduler(settings)
    add_startup_catchups(scheduler, settings)
    scheduler.start()


if __name__ == "__main__":
    main()
