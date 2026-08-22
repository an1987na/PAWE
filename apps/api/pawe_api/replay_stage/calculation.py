"""Point-in-time calculations for staged historical replay outputs.

The calculation layer deliberately has no dependency on the worker.  It reads
only data visible at the stage's simulated information boundary and limits
provider versions by the real retrieval cutoff.  Persistence helpers write to
the replay-only tables; formal decisions, briefs, approvals and publication
events are never touched here.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from pawe_api.briefs.repository import BriefGenerationError, _brief_inputs
from pawe_api.briefs.service import build_deterministic_brief_item
from pawe_api.contracts import DailyBriefItem, DataQuality, MarketState
from pawe_api.data.calendar import SHANGHAI
from pawe_api.data.classification_repository import (
    SqlClassificationRepository,
    StoredPrimaryClassification,
)
from pawe_api.data.snapshot import FrozenSnapshot
from pawe_api.db import models
from pawe_api.evaluation.repository import (
    ComputedWeeklyReview,
    ReviewTarget,
    compute_weekly_review,
)
from pawe_api.evaluation.weekly import WeeklyBar
from pawe_api.experiments.historical_week import (
    REPLAY_WARNINGS,
    HistoricalWeekReplayError,
    _observations,
    _weekly_bars,
)
from pawe_api.features.market_snapshot import TechnicalSnapshotObservation
from pawe_api.features.sector_market import build_classified_market_observations
from pawe_api.features.weekly import build_degraded_market_state_input, build_rule_features
from pawe_api.rules.engine import RULE_VERSION, run_v9_rules
from pawe_api.rules.models import ScoredCandidate

CURRENT_V9_FALLBACK_WARNING = "HISTORICAL_RULE_REGISTRY_UNAVAILABLE_USING_CURRENT_V9"


class StagedReplayCalculationError(RuntimeError):
    """A data or gate error that is safe to record on one replay stage."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "REPLAY_DATA_INCOMPLETE",
        warnings: tuple[str, ...] = (),
        coverage: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.warnings = warnings
        self.coverage = coverage or {}


@dataclass(frozen=True, slots=True)
class ReplayWeekContext:
    week_id: date
    first_open_date: date
    final_open_date: date
    previous_open_date: date
    open_dates: frozenset[date]


@dataclass(frozen=True, slots=True)
class SelectionCalculation:
    candidates: tuple[ScoredCandidate, ...]
    classifications: dict[str, StoredPrimaryClassification]
    stocks: dict[str, models.Stock]
    fingerprint: str
    warnings: tuple[str, ...]
    coverage: dict[str, object]


@dataclass(frozen=True, slots=True)
class DailyBriefCalculation:
    decision_set_id: uuid.UUID
    trade_date: date
    items: tuple[tuple[models.Stock, DailyBriefItem], ...]
    quality: DataQuality
    fingerprint: str
    warnings: tuple[str, ...]
    coverage: dict[str, object]


@dataclass(frozen=True, slots=True)
class WeeklyReviewCalculation:
    decision_set_id: uuid.UUID
    computed: ComputedWeeklyReview
    fingerprint: str
    warnings: tuple[str, ...]
    coverage: dict[str, object]


class StagedReplayCalculationService:
    """Small dependency-injectable facade for the three replay calculations."""

    async def weekly_selection(
        self,
        session: AsyncSession,
        replay: models.ReplayRun,
        *,
        actual_run_at: datetime,
    ) -> SelectionCalculation:
        return await calculate_weekly_selection(
            session, replay, actual_run_at=actual_run_at
        )

    async def daily_brief(
        self,
        session: AsyncSession,
        replay: models.ReplayRun,
        stage: models.ReplayStageRun,
        *,
        actual_run_at: datetime,
    ) -> DailyBriefCalculation:
        return await calculate_daily_brief(
            session, replay, stage, actual_run_at=actual_run_at
        )

    async def weekly_review(
        self,
        session: AsyncSession,
        replay: models.ReplayRun,
        stage: models.ReplayStageRun,
        *,
        actual_run_at: datetime,
        benchmark_return: float | None = None,
    ) -> WeeklyReviewCalculation:
        return await calculate_weekly_review(
            session,
            replay,
            stage,
            actual_run_at=actual_run_at,
            benchmark_return=benchmark_return,
        )


def point_in_time_payload(payload: dict[str, object], *, as_of: date) -> dict[str, object]:
    """Remove bars newer than the business boundary before hashing or scoring."""
    bounded = dict(payload)
    source_bars = payload.get("source_bars")
    if not isinstance(source_bars, dict):
        return bounded
    bounded["source_bars"] = {
        source: _bounded_bars(bars, as_of)
        for source, bars in source_bars.items()
        if isinstance(bars, list)
    }
    return bounded


def _bounded_bars(bars: list[object], as_of: date) -> list[object]:
    bounded: list[object] = []
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        bar_date = _bar_date(bar)
        if bar_date is not None and bar_date <= as_of:
            bounded.append(bar)
    return bounded


def _bar_date(bar: dict[str, object]) -> date | None:
    raw = bar.get("trade_date")
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


async def load_replay_week_context(
    session: AsyncSession,
    week_id: date,
) -> ReplayWeekContext:
    rows = list(
        await session.scalars(
            select(models.TradingCalendar)
            .where(
                models.TradingCalendar.trade_date >= week_id,
                models.TradingCalendar.trade_date <= week_id + timedelta(days=4),
            )
            .order_by(models.TradingCalendar.trade_date)
        )
    )
    if len(rows) != 5:
        raise StagedReplayCalculationError(
            "replay requires a complete Monday-to-Friday calendar",
            code="REPLAY_CALENDAR_INCOMPLETE",
            warnings=("REPLAY_CALENDAR_INCOMPLETE",),
        )
    open_rows = [row for row in rows if row.is_open]
    if len(open_rows) < 3 or open_rows[0].previous_open_date is None:
        raise StagedReplayCalculationError(
            "replay requires at least three trading days and a previous open date",
            code="REPLAY_CALENDAR_INCOMPLETE",
            warnings=("REPLAY_CALENDAR_INCOMPLETE",),
        )
    return ReplayWeekContext(
        week_id=week_id,
        first_open_date=open_rows[0].trade_date,
        final_open_date=open_rows[-1].trade_date,
        previous_open_date=open_rows[0].previous_open_date,
        open_dates=frozenset(row.trade_date for row in open_rows),
    )


async def calculate_weekly_selection(
    session: AsyncSession,
    replay: models.ReplayRun,
    *,
    actual_run_at: datetime,
) -> SelectionCalculation:
    _require_aware(actual_run_at)
    context = await load_replay_week_context(session, replay.week_id)
    decision_cutoff = datetime.combine(context.previous_open_date, time(15), tzinfo=SHANGHAI)
    stocks = list(
        await session.scalars(
            select(models.Stock)
            .where(
                models.Stock.status == "active",
                models.Stock.exchange.in_(["SSE", "SZSE"]),
                models.Stock.board != "star",
                models.Stock.listing_date <= context.previous_open_date,
                or_(models.Stock.fetched_at.is_(None), models.Stock.fetched_at <= actual_run_at),
            )
            .order_by(models.Stock.code)
        )
    )
    if not stocks:
        raise StagedReplayCalculationError(
            "no point-in-time stocks are available",
            code="REPLAY_SELECTION_DATA_INCOMPLETE",
            warnings=("REPLAY_SELECTION_COVERAGE_0",),
            coverage={"universe_count": 0, "classified_count": 0},
        )
    classifications = await SqlClassificationRepository(session).load_primary_information_as_of(
        available_on=context.first_open_date,
        published_by=context.previous_open_date,
        retrieved_by=actual_run_at,
        stock_ids=tuple(stock.id for stock in stocks),
    )
    classified_stocks = [stock for stock in stocks if stock.code in classifications]
    if not classified_stocks:
        raise StagedReplayCalculationError(
            "no point-in-time classifications are available",
            code="REPLAY_SELECTION_DATA_INCOMPLETE",
            warnings=("REPLAY_SELECTION_CLASSIFICATION_COVERAGE_0",),
            coverage={"universe_count": len(stocks), "classified_count": 0},
        )
    observations, unavailable = await _observations_with_coverage(
        session,
        classified_stocks,
        as_of=context.previous_open_date,
        retrieved_by=actual_run_at,
    )
    if unavailable or len(observations) != len(classified_stocks):
        raise StagedReplayCalculationError(
            "selection observation coverage is incomplete",
            code="REPLAY_SELECTION_DATA_INCOMPLETE",
            warnings=(f"REPLAY_SELECTION_OBSERVATION_MISSING_{len(unavailable)}",),
            coverage={
                "universe_count": len(stocks),
                "classified_count": len(classified_stocks),
                "observation_count": len(observations),
                "missing_codes": unavailable,
            },
        )
    market_observations = build_classified_market_observations(observations, classifications)
    stock_by_id = {stock.id: stock for stock in classified_stocks}
    features_with_ids = build_rule_features(
        [
            (
                item.technical.stock_id,
                item.technical.stock_code,
                stock_by_id[item.technical.stock_id].name,
                stock_by_id[item.technical.stock_id].board,
                stock_by_id[item.technical.stock_id].status,
                stock_by_id[item.technical.stock_id].listing_date,
                point_in_time_payload(
                    item.snapshot_payload(), as_of=context.previous_open_date
                ),
                item.technical.quality,
            )
            for item in market_observations
        ]
    )
    snapshot_hash = _hash(
        {
            "week_id": replay.week_id.isoformat(),
            "information_cutoff": decision_cutoff.isoformat(),
            "stocks": [stock.code for stock in classified_stocks],
            "observations": [
                {
                    "code": item.stock_code,
                    "as_of": item.as_of.isoformat(),
                    "payload": point_in_time_payload(
                        item.payload, as_of=context.previous_open_date
                    ),
                }
                for item in observations
            ],
            "classification_hashes": sorted(
                item.content_hash for item in classifications.values()
            ),
        }
    )
    rule_result = run_v9_rules(
        snapshot=FrozenSnapshot(
            cutoff=decision_cutoff,
            locked_at=actual_run_at,
            content_hash=snapshot_hash,
            records=(),
        ),
        features=[feature for _, feature in features_with_ids],
        market_state_input=build_degraded_market_state_input(MarketState.NORMAL),
    )
    selected = rule_result.baseline.items
    if not selected:
        raise StagedReplayCalculationError(
            "V9 produced no eligible replay targets",
            code="REPLAY_SELECTION_NO_TARGETS",
            warnings=("REPLAY_SELECTION_NO_TARGETS",),
            coverage={
                "universe_count": len(stocks),
                "classified_count": len(classified_stocks),
                "observation_count": len(observations),
                "candidate_count": len(rule_result.candidates),
            },
        )
    warnings = tuple(
        dict.fromkeys((*REPLAY_WARNINGS, CURRENT_V9_FALLBACK_WARNING, *rule_result.flags))
    )
    return SelectionCalculation(
        candidates=selected,
        classifications=classifications,
        stocks={stock.code: stock for stock in classified_stocks},
        fingerprint=rule_result.fingerprint,
        warnings=warnings,
        coverage={
            "universe_count": len(stocks),
            "classified_count": len(classified_stocks),
            "observation_count": len(observations),
            "candidate_count": len(rule_result.candidates),
            "selected_count": len(selected),
            "information_cutoff": decision_cutoff.isoformat(),
            "retrieved_cutoff": actual_run_at.isoformat(),
        },
    )


async def calculate_daily_brief(
    session: AsyncSession,
    replay: models.ReplayRun,
    stage: models.ReplayStageRun,
    *,
    actual_run_at: datetime,
) -> DailyBriefCalculation:
    _require_aware(actual_run_at)
    if stage.trade_date is None:
        raise StagedReplayCalculationError(
            "daily replay stage has no trade_date", code="REPLAY_DAILY_DATE_MISSING"
        )
    context = await load_replay_week_context(session, replay.week_id)
    if stage.trade_date not in context.open_dates:
        raise StagedReplayCalculationError(
            "daily replay target is not an open trading day",
            code="REPLAY_DAILY_DATE_INVALID",
        )
    decision_set, rows = await _selection_rows(session, replay.id)
    if decision_set is None or not rows:
        raise StagedReplayCalculationError(
            "weekly selection output is missing",
            code="REPLAY_SELECTION_OUTPUT_MISSING",
            warnings=("REPLAY_SELECTION_OUTPUT_MISSING",),
        )
    stocks = [stock for _, stock in rows]
    observations, unavailable = await _observations_with_coverage(
        session,
        stocks,
        as_of=stage.trade_date,
        retrieved_by=actual_run_at,
    )
    by_code = {item.stock_code: item for item in observations}
    if unavailable or len(by_code) != len(stocks):
        raise StagedReplayCalculationError(
            "daily observation coverage is incomplete",
            code="REPLAY_DAILY_DATA_INCOMPLETE",
            warnings=(f"REPLAY_DAILY_OBSERVATION_MISSING_{len(unavailable)}",),
            coverage={
                "selected_count": len(stocks),
                "observation_count": len(observations),
                "missing_codes": unavailable,
            },
        )
    generated: list[tuple[models.Stock, DailyBriefItem]] = []
    invalid_codes: list[str] = []
    invalid_reasons: dict[str, str] = {}
    payload_hashes: list[dict[str, object]] = []
    for _decision_item, stock in rows:
        observation = by_code[stock.code]
        try:
            target, market = _brief_inputs(
                stock.code,
                stock.name,
                observation.payload,
                week_id=replay.week_id,
                trade_date=stage.trade_date,
                quality=observation.quality,
            )
            generated.append((stock, build_deterministic_brief_item(target, market)))
        except (BriefGenerationError, KeyError, TypeError, ValueError) as exc:
            invalid_codes.append(stock.code)
            invalid_reasons[stock.code] = type(exc).__name__
        payload_hashes.append({"code": stock.code, "payload": observation.payload})
    if invalid_codes:
        raise StagedReplayCalculationError(
            "daily brief inputs are incomplete",
            code="REPLAY_DAILY_DATA_INCOMPLETE",
            warnings=("REPLAY_DAILY_BRIEF_INPUTS_INCOMPLETE",),
            coverage={
                "selected_count": len(stocks),
                "observation_count": len(observations),
                "missing_codes": invalid_codes,
                "input_errors": invalid_reasons,
            },
        )
    fingerprint = _hash(
        {
            "selection_fingerprint": decision_set.fingerprint,
            "trade_date": stage.trade_date.isoformat(),
            "observations": payload_hashes,
        }
    )
    return DailyBriefCalculation(
        decision_set_id=decision_set.id,
        trade_date=stage.trade_date,
        items=tuple(generated),
        quality=DataQuality.DEGRADED,
        fingerprint=fingerprint,
        warnings=(*REPLAY_WARNINGS, CURRENT_V9_FALLBACK_WARNING),
        coverage={
            "selected_count": len(stocks),
            "observation_count": len(observations),
            "information_cutoff": stage.information_cutoff.isoformat(),
            "retrieved_cutoff": actual_run_at.isoformat(),
        },
    )


async def calculate_weekly_review(
    session: AsyncSession,
    replay: models.ReplayRun,
    stage: models.ReplayStageRun,
    *,
    actual_run_at: datetime,
    benchmark_return: float | None = None,
) -> WeeklyReviewCalculation:
    _require_aware(actual_run_at)
    context = await load_replay_week_context(session, replay.week_id)
    review_at = datetime.combine(context.final_open_date, time(15, 30), tzinfo=SHANGHAI)
    if actual_run_at < review_at:
        raise StagedReplayCalculationError(
            "weekly review is not due until the final trading day closes",
            code="REPLAY_WEEKLY_REVIEW_NOT_DUE",
            warnings=("REPLAY_WEEKLY_REVIEW_NOT_DUE",),
        )
    decision_set, rows = await _selection_rows(session, replay.id)
    if decision_set is None or not rows:
        raise StagedReplayCalculationError(
            "weekly selection output is missing",
            code="REPLAY_SELECTION_OUTPUT_MISSING",
            warnings=("REPLAY_SELECTION_OUTPUT_MISSING",),
        )
    selected_stocks = [stock for _, stock in rows]
    selected_classifications = await SqlClassificationRepository(
        session
    ).load_primary_information_as_of(
        available_on=context.first_open_date,
        published_by=context.previous_open_date,
        retrieved_by=actual_run_at,
        stock_ids=tuple(stock.id for stock in selected_stocks),
    )
    missing_classifications = [
        stock.code for stock in selected_stocks if stock.code not in selected_classifications
    ]
    if missing_classifications:
        raise StagedReplayCalculationError(
            "weekly review classifications are incomplete",
            code="REPLAY_REVIEW_DATA_INCOMPLETE",
            warnings=("REPLAY_REVIEW_CLASSIFICATION_INCOMPLETE",),
            coverage={"missing_codes": [], "unavailable_classifications": missing_classifications},
        )
    universe = list(
        await session.scalars(
            select(models.Stock)
            .where(
                models.Stock.status == "active",
                models.Stock.exchange.in_(["SSE", "SZSE"]),
                models.Stock.board != "star",
                models.Stock.listing_date <= context.first_open_date,
                or_(models.Stock.fetched_at.is_(None), models.Stock.fetched_at <= actual_run_at),
            )
            .order_by(models.Stock.code)
        )
    )
    selected_sectors = {
        selected_classifications[stock.code].sector_code for stock in selected_stocks
    }
    peer_classifications = await SqlClassificationRepository(
        session
    ).load_primary_information_as_of(
        available_on=context.first_open_date,
        published_by=context.previous_open_date,
        retrieved_by=actual_run_at,
        stock_ids=tuple(stock.id for stock in universe),
    )
    sector_stocks = [
        stock
        for stock in universe
        if stock.code in peer_classifications
        and peer_classifications[stock.code].sector_code in selected_sectors
    ]
    observations, unavailable = await _observations_with_coverage(
        session,
        sector_stocks,
        as_of=context.final_open_date,
        retrieved_by=actual_run_at,
    )
    by_code = {item.stock_code: item for item in observations}
    missing_selected = [stock.code for stock in selected_stocks if stock.code not in by_code]
    if missing_selected or unavailable:
        missing_codes = list(dict.fromkeys((*missing_selected, *unavailable)))
        raise StagedReplayCalculationError(
            "weekly review observation coverage is incomplete",
            code="REPLAY_REVIEW_DATA_INCOMPLETE",
            warnings=("REPLAY_REVIEW_OBSERVATION_INCOMPLETE",),
            coverage={"missing_codes": missing_codes, "observation_count": len(observations)},
        )
    weekly_bars: dict[str, tuple[WeeklyBar, ...]] = {}
    missing_bar_codes: list[str] = []
    for observation in observations:
        try:
            weekly_bars[observation.stock_code] = _weekly_bars(
                observation, set(context.open_dates)
            )
        except (HistoricalWeekReplayError, KeyError, TypeError, ValueError):
            missing_bar_codes.append(observation.stock_code)
    missing_selected = [stock.code for stock in selected_stocks if stock.code not in weekly_bars]
    if missing_selected or missing_bar_codes:
        missing_codes = list(dict.fromkeys((*missing_selected, *missing_bar_codes)))
        raise StagedReplayCalculationError(
            "weekly review bars are incomplete",
            code="REPLAY_REVIEW_DATA_INCOMPLETE",
            warnings=("REPLAY_REVIEW_BARS_INCOMPLETE",),
            coverage={"missing_codes": missing_codes},
        )
    sector_returns: dict[str, list[float]] = {sector: [] for sector in selected_sectors}
    for observation in observations:
        bars = weekly_bars.get(observation.stock_code)
        if bars is None:
            continue
        sector = peer_classifications[observation.stock_code].sector_code
        sector_returns[sector].append(bars[-1].close / bars[0].open - 1)
    sector_average = {
        sector: sum(values) / len(values) for sector, values in sector_returns.items() if values
    }
    targets = tuple(
        ReviewTarget(
            stock_id=stock.id,
            stock_code=stock.code,
            stock_name=stock.name,
            rank=decision_item.rank,
            bars=weekly_bars[stock.code],
            industry_return=sector_average.get(selected_classifications[stock.code].sector_code),
        )
        for decision_item, stock in rows
    )
    warnings = [*REPLAY_WARNINGS, CURRENT_V9_FALLBACK_WARNING]
    if benchmark_return is None:
        warnings.append("CSI300_BENCHMARK_UNAVAILABLE")
    computed = compute_weekly_review(
        week_id=replay.week_id,
        source_type="historical_replay",
        source_version=1,
        rule_version=RULE_VERSION,
        targets=targets,
        as_of=review_at,
        generated_at=actual_run_at,
        quality=DataQuality.DEGRADED,
        benchmark_return=benchmark_return,
        warnings=tuple(dict.fromkeys(warnings)),
    )
    fingerprint = _hash(
        {
            "selection_fingerprint": decision_set.fingerprint,
            "open_dates": sorted(day.isoformat() for day in context.open_dates),
            "observations": [
                {"code": code, "payload": by_code[code].payload}
                for code in sorted(by_code)
            ],
            "benchmark_return": benchmark_return,
        }
    )
    return WeeklyReviewCalculation(
        decision_set_id=decision_set.id,
        computed=computed,
        fingerprint=fingerprint,
        warnings=tuple(dict.fromkeys(warnings)),
        coverage={
            "selected_count": len(selected_stocks),
            "sector_stock_count": len(sector_stocks),
            "observation_count": len(observations),
            "missing_peer_codes": [*unavailable, *missing_bar_codes],
            "information_cutoff": stage.information_cutoff.isoformat(),
            "retrieved_cutoff": actual_run_at.isoformat(),
        },
    )


async def _selection_rows(
    session: AsyncSession,
    replay_id: uuid.UUID,
) -> tuple[models.ReplayDecisionSet | None, list[tuple[models.ReplayDecisionItem, models.Stock]]]:
    decision_set = await session.scalar(
        select(models.ReplayDecisionSet)
        .where(models.ReplayDecisionSet.replay_run_id == replay_id)
        .order_by(models.ReplayDecisionSet.version.desc())
        .limit(1)
    )
    if decision_set is None:
        return None, []
    result = await session.execute(
        select(models.ReplayDecisionItem, models.Stock)
        .join(models.Stock, models.Stock.id == models.ReplayDecisionItem.stock_id)
        .where(models.ReplayDecisionItem.replay_decision_set_id == decision_set.id)
        .order_by(models.ReplayDecisionItem.rank)
    )
    return decision_set, [(item, stock) for item, stock in result.all()]


async def _observations_with_coverage(
    session: AsyncSession,
    stocks: list[models.Stock],
    *,
    as_of: date,
    retrieved_by: datetime,
) -> tuple[list[TechnicalSnapshotObservation], list[str]]:
    observations: list[TechnicalSnapshotObservation] = []
    unavailable: list[str] = []
    for stock in stocks:
        try:
            observations.extend(
                await _observations(
                    session,
                    [stock],
                    as_of=as_of,
                    retrieved_by=retrieved_by,
                )
            )
        except (RuntimeError, ValueError, KeyError, TypeError, LookupError):
            unavailable.append(stock.code)
    return observations, unavailable


async def persist_selection(
    session: AsyncSession,
    replay: models.ReplayRun,
    stage: models.ReplayStageRun,
    calculation: SelectionCalculation,
    *,
    actual_run_at: datetime,
) -> models.ReplayDecisionSet:
    """Persist a real selection result into replay-only tables."""
    decision_set = await session.scalar(
        select(models.ReplayDecisionSet).where(
            models.ReplayDecisionSet.replay_stage_run_id == stage.id
        )
    )
    if decision_set is None:
        decision_set = models.ReplayDecisionSet(
            id=uuid.uuid4(),
            replay_run_id=replay.id,
            replay_stage_run_id=stage.id,
            week_id=replay.week_id,
            version=1,
            status="degraded",
            fingerprint=calculation.fingerprint,
            rule_version=RULE_VERSION,
            information_cutoff=stage.information_cutoff,
            created_at=actual_run_at,
        )
        session.add(decision_set)
        await session.flush()
    else:
        decision_set.fingerprint = calculation.fingerprint
        decision_set.status = "degraded"
    existing_codes = set(
        await session.scalars(
            select(models.Stock.code)
            .join(
                models.ReplayDecisionItem,
                models.ReplayDecisionItem.stock_id == models.Stock.id,
            )
            .where(models.ReplayDecisionItem.replay_decision_set_id == decision_set.id)
        )
    )
    for rank, candidate in enumerate(calculation.candidates, start=1):
        code = candidate.features.stock_code
        stock = calculation.stocks[code]
        if code in existing_codes:
            continue
        session.add(
            models.ReplayDecisionItem(
                id=uuid.uuid4(),
                replay_decision_set_id=decision_set.id,
                stock_id=stock.id,
                rank=rank,
                role=candidate.bucket.value,
                target_return=Decimal("0.10"),
                confidence="low",
                summary=(
                    f"V9规则回溯候选，规则分 {candidate.rule_score:.2f}；"
                    "结果仅供历史研究。"
                ),
                primary_risk="历史规则注册未接入，当前使用 V9 降级入口。",
            )
        )
    stage.input_fingerprint = calculation.fingerprint
    stage.warnings = list(dict.fromkeys((*stage.warnings, *calculation.warnings)))
    stage.details = stage.details | {
        "calculation": "v9_point_in_time",
        "coverage": calculation.coverage,
        "selected_codes": [item.features.stock_code for item in calculation.candidates],
        "output_count": len(calculation.candidates),
    }
    await session.flush()
    return decision_set


async def persist_daily_brief(
    session: AsyncSession,
    replay: models.ReplayRun,
    stage: models.ReplayStageRun,
    calculation: DailyBriefCalculation,
    *,
    actual_run_at: datetime,
) -> models.ReplayDailyBrief:
    brief = await session.scalar(
        select(models.ReplayDailyBrief).where(
            models.ReplayDailyBrief.replay_stage_run_id == stage.id,
            models.ReplayDailyBrief.trade_date == calculation.trade_date,
        )
    )
    if brief is None:
        brief = models.ReplayDailyBrief(
            id=uuid.uuid4(),
            replay_run_id=replay.id,
            replay_stage_run_id=stage.id,
            replay_decision_set_id=calculation.decision_set_id,
            week_id=replay.week_id,
            trade_date=calculation.trade_date,
            version=1,
            status="degraded",
            as_of=stage.information_cutoff,
            fetched_at=actual_run_at,
            quality=calculation.quality.value,
            ai_degraded=True,
            summary="点时 V9 回溯日报；结果仅供历史研究。",
        )
        session.add(brief)
        await session.flush()
    existing_stock_ids = set(
        await session.scalars(
            select(models.ReplayDailyBriefItem.stock_id).where(
                models.ReplayDailyBriefItem.replay_daily_brief_id == brief.id
            )
        )
    )
    for stock, item in calculation.items:
        if stock.id in existing_stock_ids:
            continue
        session.add(
            models.ReplayDailyBriefItem(
                id=uuid.uuid4(),
                replay_daily_brief_id=brief.id,
                stock_id=stock.id,
                daily_return=Decimal(str(item.daily_return)),
                week_to_date_return=Decimal(str(item.week_to_date_return)),
                week_high_return=Decimal(str(item.week_high_return)),
                drawdown_from_week_high=Decimal(str(item.drawdown_from_week_high)),
                distance_to_target=Decimal(str(item.distance_to_target)),
                volume_activity=(
                    Decimal(str(item.volume_activity))
                    if item.volume_activity is not None
                    else None
                ),
                risk_status=item.risk_status.value,
                summary=item.summary,
                evidence_ids=item.evidence_ids,
            )
        )
    stage.input_fingerprint = calculation.fingerprint
    stage.warnings = list(dict.fromkeys((*stage.warnings, *calculation.warnings)))
    stage.details = stage.details | {
        "calculation": "v9_point_in_time",
        "coverage": calculation.coverage,
        "output_count": len(calculation.items),
    }
    await session.flush()
    return brief


async def persist_weekly_review(
    session: AsyncSession,
    replay: models.ReplayRun,
    stage: models.ReplayStageRun,
    calculation: WeeklyReviewCalculation,
    *,
    actual_run_at: datetime,
) -> models.ReplayWeeklyReview:
    computed = calculation.computed
    review = await session.scalar(
        select(models.ReplayWeeklyReview).where(
            models.ReplayWeeklyReview.replay_stage_run_id == stage.id
        )
    )
    if review is None:
        review = models.ReplayWeeklyReview(
            id=uuid.uuid4(),
            replay_run_id=replay.id,
            replay_stage_run_id=stage.id,
            replay_decision_set_id=calculation.decision_set_id,
            week_id=replay.week_id,
            status=computed.status,
            entry_trade_date=computed.entry_trade_date,
            final_trade_date=computed.final_trade_date,
            as_of=computed.as_of,
            generated_at=actual_run_at,
            quality=computed.quality.value,
            aggregate=computed.aggregate,
            summary=computed.summary,
            warnings=list(computed.warnings),
        )
        session.add(review)
        await session.flush()
    existing_stock_ids = set(
        await session.scalars(
            select(models.ReplayWeeklyReviewItem.stock_id).where(
                models.ReplayWeeklyReviewItem.replay_weekly_review_id == review.id
            )
        )
    )
    for evaluated in computed.items:
        if evaluated.target.stock_id in existing_stock_ids:
            continue
        performance = evaluated.performance
        benchmark_excess = (
            performance.week_close_return - evaluated.benchmark_return
            if evaluated.benchmark_return is not None
            else None
        )
        industry_excess = (
            performance.week_close_return - evaluated.target.industry_return
            if evaluated.target.industry_return is not None
            else None
        )
        session.add(
            models.ReplayWeeklyReviewItem(
                id=uuid.uuid4(),
                replay_weekly_review_id=review.id,
                stock_id=evaluated.target.stock_id,
                rank=evaluated.target.rank,
                entry_price=Decimal(str(performance.entry_price)),
                week_high_return=Decimal(str(performance.week_high_return)),
                week_close_return=Decimal(str(performance.week_close_return)),
                max_drawdown_from_entry=Decimal(str(performance.max_drawdown_from_entry)),
                max_peak_to_trough_drawdown=Decimal(
                    str(performance.max_peak_to_trough_drawdown)
                ),
                target_touched=performance.target_touched,
                target_touch_date=performance.target_touch_date,
                drawdown_before_touch=(
                    Decimal(str(performance.drawdown_before_touch))
                    if performance.drawdown_before_touch is not None
                    else None
                ),
                accessible_at_entry=performance.accessible_at_entry,
                benchmark_return=(
                    Decimal(str(evaluated.benchmark_return))
                    if evaluated.benchmark_return is not None
                    else None
                ),
                benchmark_excess=(
                    Decimal(str(benchmark_excess)) if benchmark_excess is not None else None
                ),
                industry_return=(
                    Decimal(str(evaluated.target.industry_return))
                    if evaluated.target.industry_return is not None
                    else None
                ),
                industry_excess=(
                    Decimal(str(industry_excess)) if industry_excess is not None else None
                ),
            )
        )
    stage.input_fingerprint = calculation.fingerprint
    stage.warnings = list(dict.fromkeys((*stage.warnings, *calculation.warnings)))
    stage.details = stage.details | {
        "calculation": "v9_point_in_time",
        "coverage": calculation.coverage,
        "aggregate": computed.aggregate,
        "output_count": len(computed.items),
    }
    await session.flush()
    return review


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("actual_run_at must be timezone-aware")


def _hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
