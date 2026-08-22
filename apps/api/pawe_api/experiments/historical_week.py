import hashlib
import json
import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pawe_api.briefs.repository import _brief_inputs
from pawe_api.briefs.service import build_deterministic_brief_item
from pawe_api.contracts import DailyBrief, DataQuality, HistoricalReplayResponse, MarketState
from pawe_api.data.calendar import SHANGHAI
from pawe_api.data.classification_repository import SqlClassificationRepository
from pawe_api.data.snapshot import FrozenSnapshot
from pawe_api.db import models
from pawe_api.evaluation.repository import (
    ReviewTarget,
    SqlWeeklyReviewApplication,
    compute_weekly_review,
)
from pawe_api.evaluation.weekly import WeeklyBar
from pawe_api.features.market_snapshot import (
    DailyBriefObservation,
    TechnicalSnapshotObservation,
    build_retrospective_technical_observation,
)
from pawe_api.features.sector_market import build_classified_market_observations
from pawe_api.features.weekly import build_degraded_market_state_input, build_rule_features
from pawe_api.rules.engine import RULE_VERSION, run_v9_rules

REPLAY_WARNINGS = (
    "RETROSPECTIVE_FETCH_AFTER_SIMULATED_TIME",
    "QFQ_VINTAGE_NOT_CAPTURED_AT_ORIGINAL_TIME",
    "STATE_INPUT_DEGRADED_NO_PRIOR_FORMAL_REVIEW",
)


class HistoricalWeekReplayError(RuntimeError):
    pass


class HistoricalWeekReplayApplication:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.session_factory = session_factory

    async def get(self, week_id: date) -> HistoricalReplayResponse | None:
        async with self.session_factory() as session:
            replay = await session.scalar(
                select(models.HistoricalReplay).where(
                    models.HistoricalReplay.week_id == week_id,
                    models.HistoricalReplay.rule_version == RULE_VERSION,
                )
            )
            if replay is None:
                return None
            return await self._response(session, replay)

    async def run(
        self,
        week_id: date,
        *,
        actual_run_at: datetime | None = None,
        benchmark_return: float | None = None,
    ) -> HistoricalReplayResponse:
        if week_id.weekday() != 0:
            raise HistoricalWeekReplayError("week_id must be a Monday")
        existing = await self.get(week_id)
        if existing is not None:
            return existing
        run_at = actual_run_at or datetime.now(UTC)
        if run_at.tzinfo is None or run_at.utcoffset() is None:
            raise HistoricalWeekReplayError("actual_run_at must be timezone-aware")

        async with self.session_factory() as session:
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
            if len(calendar) != 5:
                raise HistoricalWeekReplayError("historical trading calendar is incomplete")
            open_rows = [row for row in calendar if row.is_open]
            if len(open_rows) < 3:
                raise HistoricalWeekReplayError("natural week has fewer than three trading days")
            previous_open = open_rows[0].previous_open_date
            if previous_open is None:
                raise HistoricalWeekReplayError("previous open date is missing")
            decision_cutoff = datetime.combine(previous_open, time(15), tzinfo=SHANGHAI)
            selection_at = datetime.combine(open_rows[0].trade_date, time(8, 30), tzinfo=SHANGHAI)
            review_at = datetime.combine(open_rows[-1].trade_date, time(15, 30), tzinfo=SHANGHAI)
            if run_at < review_at:
                raise HistoricalWeekReplayError(
                    "replay cannot run before the simulated review time"
                )

            stocks = list(
                await session.scalars(
                    select(models.Stock)
                    .where(
                        models.Stock.status == "active",
                        models.Stock.exchange.in_(["SSE", "SZSE"]),
                        models.Stock.board != "star",
                    )
                    .order_by(models.Stock.code)
                )
            )
            classifications = await SqlClassificationRepository(
                session
            ).load_primary_information_as_of(
                available_on=open_rows[0].trade_date,
                published_by=previous_open,
                retrieved_by=run_at,
                stock_ids=tuple(stock.id for stock in stocks),
            )
            classified = [stock for stock in stocks if stock.code in classifications]
            if not classified:
                raise HistoricalWeekReplayError("no point-in-time classified stocks are available")
            selection_observations = await _observations(
                session,
                classified,
                as_of=previous_open,
                retrieved_by=run_at,
            )
            if len(selection_observations) != len(classified):
                raise HistoricalWeekReplayError("selection observation coverage is incomplete")
            market_observations = build_classified_market_observations(
                selection_observations,
                classifications,
            )
            stock_by_id = {stock.id: stock for stock in classified}
            features_with_ids = build_rule_features(
                [
                    (
                        item.technical.stock_id,
                        item.technical.stock_code,
                        stock_by_id[item.technical.stock_id].name,
                        stock_by_id[item.technical.stock_id].board,
                        stock_by_id[item.technical.stock_id].status,
                        stock_by_id[item.technical.stock_id].listing_date,
                        item.snapshot_payload(),
                        item.technical.quality,
                    )
                    for item in market_observations
                ]
            )
            snapshot_hash = _hash(
                {
                    "week_id": week_id.isoformat(),
                    "information_cutoff": decision_cutoff.isoformat(),
                    "codes": [item.stock_code for item in selection_observations],
                    "classification_hashes": sorted(
                        item.content_hash for item in classifications.values()
                    ),
                }
            )
            rule_result = run_v9_rules(
                snapshot=FrozenSnapshot(
                    cutoff=decision_cutoff,
                    locked_at=run_at,
                    content_hash=snapshot_hash,
                    records=(),
                ),
                features=[feature for _, feature in features_with_ids],
                market_state_input=build_degraded_market_state_input(MarketState.NORMAL),
            )
            selected = rule_result.baseline.items
            if not selected:
                raise HistoricalWeekReplayError("V9 produced no eligible replay targets")
            stock_by_code = {stock.code: stock for stock in classified}

            daily_briefs: list[DailyBrief] = []
            daily_information_boundaries: dict[str, str] = {}
            final_observations: dict[str, TechnicalSnapshotObservation] = {}
            for open_row in open_rows:
                day_observations = await _observations(
                    session,
                    [stock_by_code[item.features.stock_code] for item in selected],
                    as_of=open_row.trade_date,
                    retrieved_by=run_at,
                )
                by_code = {item.stock_code: item for item in day_observations}
                if any(item.as_of > open_row.trade_date for item in day_observations):
                    raise HistoricalWeekReplayError("daily observation exceeds simulated date")
                daily_information_boundaries[open_row.trade_date.isoformat()] = max(
                    item.as_of for item in day_observations
                ).isoformat()
                items = []
                for candidate in selected:
                    code = candidate.features.stock_code
                    observation = by_code[code]
                    target, market = _brief_inputs(
                        code,
                        candidate.features.stock_name,
                        observation.payload,
                        week_id=week_id,
                        trade_date=open_row.trade_date,
                        quality=DataQuality.DEGRADED,
                    )
                    items.append(build_deterministic_brief_item(target, market))
                    if open_row is open_rows[-1]:
                        final_observations[code] = observation
                daily_briefs.append(
                    DailyBrief(
                        week_id=week_id,
                        trade_date=open_row.trade_date,
                        decision_version=1,
                        as_of=datetime.combine(open_row.trade_date, time(15), tzinfo=SHANGHAI),
                        fetched_at=run_at,
                        quality=DataQuality.DEGRADED,
                        ai_degraded=True,
                        items=items,
                    )
                )

            selected_sectors = {
                classifications[item.features.stock_code].sector_code for item in selected
            }
            sector_stocks = [
                stock
                for stock in classified
                if classifications[stock.code].sector_code in selected_sectors
            ]
            sector_observations = await _observations(
                session,
                sector_stocks,
                as_of=open_rows[-1].trade_date,
                retrieved_by=run_at,
            )
            sector_returns: dict[str, list[float]] = {
                sector: [] for sector in selected_sectors
            }
            open_dates = {row.trade_date for row in open_rows}
            for observation in sector_observations:
                bars = _weekly_bars(observation, open_dates)
                sector = classifications[observation.stock_code].sector_code
                sector_returns[sector].append(bars[-1].close / bars[0].open - 1)
            sector_average = {
                sector: sum(returns) / len(returns)
                for sector, returns in sector_returns.items()
                if returns
            }
            targets = tuple(
                ReviewTarget(
                    stock_id=stock_by_code[candidate.features.stock_code].id,
                    stock_code=candidate.features.stock_code,
                    stock_name=candidate.features.stock_name,
                    rank=rank,
                    bars=_weekly_bars(
                        final_observations[candidate.features.stock_code],
                        open_dates,
                    ),
                    industry_return=sector_average.get(
                        classifications[candidate.features.stock_code].sector_code
                    ),
                )
                for rank, candidate in enumerate(selected, start=1)
            )
            warnings = list(REPLAY_WARNINGS)
            if benchmark_return is None:
                warnings.append("CSI300_BENCHMARK_UNAVAILABLE")
            computed = compute_weekly_review(
                week_id=week_id,
                source_type="historical_replay",
                source_version=1,
                rule_version=RULE_VERSION,
                targets=targets,
                as_of=review_at,
                generated_at=run_at,
                quality=DataQuality.DEGRADED,
                benchmark_return=benchmark_return,
                warnings=tuple(warnings),
            )
            decision_payload = {
                "information_cutoff": decision_cutoff.isoformat(),
                "selection_market_data_through": max(
                    item.as_of for item in selection_observations
                ).isoformat(),
                "daily_market_data_through": daily_information_boundaries,
                "review_market_data_through": open_rows[-1].trade_date.isoformat(),
                "actual_retrieval_time": run_at.isoformat(),
                "market_state": rule_result.market_state.value,
                "flags": list(rule_result.flags),
                "candidate_count": len(rule_result.candidates),
                "classified_universe_count": len(classified),
                "selected": [
                    {
                        "code": item.features.stock_code,
                        "name": item.features.stock_name,
                        "score": item.rule_score,
                        "sector": item.features.primary_sector,
                        "reasons": item.score_breakdown,
                    }
                    for item in selected
                ],
            }
            replay_id = uuid.uuid4()
            replay = models.HistoricalReplay(
                id=replay_id,
                week_id=week_id,
                rule_version=RULE_VERSION,
                status="degraded",
                decision_cutoff=decision_cutoff,
                simulated_selection_at=selection_at,
                simulated_review_at=review_at,
                actual_run_at=run_at,
                quality=DataQuality.DEGRADED.value,
                selected_codes=[item.features.stock_code for item in selected],
                decision_payload=decision_payload,
                daily_briefs_payload=[brief.model_dump(mode="json") for brief in daily_briefs],
                warnings=warnings,
                content_hash=_hash(
                    {
                        "snapshot_hash": snapshot_hash,
                        "fingerprint": rule_result.fingerprint,
                        "daily_briefs": [brief.model_dump(mode="json") for brief in daily_briefs],
                        "aggregate": computed.aggregate,
                    }
                ),
                created_at=run_at,
            )
            session.add(replay)
            await session.commit()

        async with self.session_factory() as session:
            await SqlWeeklyReviewApplication(session).persist(
                computed,
                replay_run_id=replay_id,
            )
        result = await self.get(week_id)
        if result is None:
            raise HistoricalWeekReplayError("replay persistence failed")
        return result

    async def _response(
        self,
        session: AsyncSession,
        replay: models.HistoricalReplay,
    ) -> HistoricalReplayResponse:
        review = await session.scalar(
            select(models.WeeklyReview).where(models.WeeklyReview.replay_run_id == replay.id)
        )
        if review is None:
            raise HistoricalWeekReplayError("replay review is missing")
        review_response = await SqlWeeklyReviewApplication(session)._response(review)
        return HistoricalReplayResponse(
            id=str(replay.id),
            week_id=replay.week_id,
            rule_version=replay.rule_version,
            status=replay.status,
            decision_cutoff=replay.decision_cutoff,
            simulated_selection_at=replay.simulated_selection_at,
            simulated_review_at=replay.simulated_review_at,
            actual_run_at=replay.actual_run_at,
            quality=replay.quality,
            selected_codes=replay.selected_codes,
            daily_briefs=[DailyBrief.model_validate(item) for item in replay.daily_briefs_payload],
            warnings=replay.warnings,
            review=review_response,
        )


async def _observations(
    session: AsyncSession,
    stocks: list[models.Stock],
    *,
    as_of: date,
    retrieved_by: datetime,
) -> list[TechnicalSnapshotObservation]:
    return [
        await build_retrospective_technical_observation(
            session,
            stock,
            as_of=as_of,
            retrieved_by=retrieved_by,
        )
        for stock in stocks
    ]


def _weekly_bars(
    observation: TechnicalSnapshotObservation | DailyBriefObservation,
    open_dates: set[date],
) -> tuple[WeeklyBar, ...]:
    source_bars = observation.payload.get("source_bars")
    if not isinstance(source_bars, dict):
        raise HistoricalWeekReplayError("weekly source bars are missing")
    raw = next(
        (
            value
            for source in ("tencent", "eastmoney", "sina")
            if isinstance((value := source_bars.get(source)), list) and value
        ),
        None,
    )
    if raw is None:
        raise HistoricalWeekReplayError("weekly source bars are unavailable")
    bars = tuple(
        WeeklyBar(
            trade_date=trade_date,
            open=float(item["open"]),
            high=float(item["high"]),
            low=float(item["low"]),
            close=float(item["close"]),
        )
        for item in raw
        if isinstance(item, dict)
        and (trade_date := date.fromisoformat(str(item["trade_date"]))) in open_dates
    )
    if {bar.trade_date for bar in bars} != open_dates:
        raise HistoricalWeekReplayError("weekly review bars are incomplete")
    return bars


def _hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
