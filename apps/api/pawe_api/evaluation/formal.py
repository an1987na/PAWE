from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pawe_api.contracts import DataQuality, WeeklyReviewResponse
from pawe_api.data.calendar import SHANGHAI
from pawe_api.data.classification_repository import SqlClassificationRepository
from pawe_api.db import models
from pawe_api.evaluation.repository import (
    ReviewTarget,
    SqlWeeklyReviewApplication,
    compute_weekly_review,
)
from pawe_api.evaluation.weekly import WeeklyBar
from pawe_api.experiments.historical_week import HistoricalWeekReplayError, _weekly_bars
from pawe_api.features.market_snapshot import build_stored_daily_brief_observation
from pawe_api.features.technical import FeatureCalculationError


class FormalWeeklyReviewError(RuntimeError):
    pass


async def _load_review_bars(
    session: AsyncSession,
    stocks: list[models.Stock],
    *,
    as_of: date,
    snapshot_cutoff: datetime,
    open_dates: set[date],
) -> tuple[
    dict[str, tuple[WeeklyBar, ...]],
    dict[str, DataQuality],
    list[str],
]:
    weekly_bars: dict[str, tuple[WeeklyBar, ...]] = {}
    observation_quality: dict[str, DataQuality] = {}
    unavailable_codes: list[str] = []
    for stock in stocks:
        try:
            observation = await build_stored_daily_brief_observation(
                session,
                stock,
                as_of=as_of,
                snapshot_cutoff=snapshot_cutoff,
            )
            weekly_bars[stock.code] = _weekly_bars(observation, open_dates)
            observation_quality[stock.code] = observation.quality
        except (FeatureCalculationError, HistoricalWeekReplayError):
            unavailable_codes.append(stock.code)
    return weekly_bars, observation_quality, unavailable_codes


async def generate_formal_weekly_reviews(
    session_factory: async_sessionmaker[AsyncSession],
    week_id: date,
    *,
    generated_at: datetime,
    benchmark_return: float | None,
) -> list[WeeklyReviewResponse]:
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise FormalWeeklyReviewError("generated_at must be timezone-aware")
    async with session_factory() as session:
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
        open_dates = tuple(row.trade_date for row in calendar if row.is_open)
        if len(calendar) != 5 or len(open_dates) < 3:
            raise FormalWeeklyReviewError("complete trading week calendar is unavailable")
        review_at = datetime.combine(open_dates[-1], time(15, 30), tzinfo=SHANGHAI)
        if generated_at < review_at:
            return []
        week = await session.get(models.Week, week_id)
        if week is None:
            return []
        decisions = list(
            await session.scalars(
                select(models.DecisionSet)
                .where(
                    models.DecisionSet.week_id == week_id,
                    models.DecisionSet.type.in_(["rule", "ai", "published"]),
                )
                .order_by(models.DecisionSet.type, models.DecisionSet.version)
            )
        )
        if not decisions:
            return []
        decision_items: dict[object, list[tuple[models.DecisionItem, models.Stock]]] = {}
        selected_stock_ids: set[int] = set()
        for decision in decisions:
            result = await session.execute(
                select(models.DecisionItem, models.Stock)
                .join(models.Stock, models.Stock.id == models.DecisionItem.stock_id)
                .where(models.DecisionItem.decision_set_id == decision.id)
                .order_by(models.DecisionItem.rank)
            )
            rows = [(item, stock) for item, stock in result.all()]
            if rows:
                decision_items[decision.id] = rows
                selected_stock_ids.update(stock.id for _, stock in rows)
        if not selected_stock_ids:
            return []

        classifications = await SqlClassificationRepository(session).load_primary_as_of(
            available_on=week_id,
            published_by=open_dates[-1],
            fetched_by=generated_at,
        )
        selected_codes = {
            stock.code for rows in decision_items.values() for _, stock in rows
        }
        unclassified_selected = sorted(selected_codes - classifications.keys())
        if unclassified_selected:
            raise FormalWeeklyReviewError(
                "formal review classifications are incomplete: "
                + ",".join(unclassified_selected)
            )
        selected_sectors = {
            classifications[code].sector_code for code in selected_codes
        }
        sector_stocks = list(
            await session.scalars(
                select(models.Stock)
                .where(
                    models.Stock.status == "active",
                    models.Stock.id.in_(
                        classification.stock_id
                        for classification in classifications.values()
                        if classification.sector_code in selected_sectors
                    ),
                )
                .order_by(models.Stock.code)
            )
        )
        open_date_set = set(open_dates)
        weekly_bars, observation_quality, unavailable_peer_codes = (
            await _load_review_bars(
                session,
                sector_stocks,
                as_of=open_dates[-1],
                snapshot_cutoff=generated_at,
                open_dates=open_date_set,
            )
        )
        sector_values: dict[str, list[float]] = {
            sector: [] for sector in selected_sectors
        }
        for code, bars in weekly_bars.items():
            sector_values[classifications[code].sector_code].append(
                bars[-1].close / bars[0].open - 1
            )
        sector_returns = {
            sector: sum(values) / len(values)
            for sector, values in sector_values.items()
            if values
        }
        computations = []
        for decision in decisions:
            rows = decision_items.get(decision.id, [])
            if not rows:
                continue
            missing = [stock.code for _, stock in rows if stock.code not in weekly_bars]
            if missing:
                raise FormalWeeklyReviewError(
                    "formal review observations are incomplete: " + ",".join(missing)
                )
            targets = tuple(
                ReviewTarget(
                    stock_id=stock.id,
                    stock_code=stock.code,
                    stock_name=stock.name,
                    rank=item.rank,
                    bars=weekly_bars[stock.code],
                    industry_return=sector_returns.get(
                        classifications[stock.code].sector_code
                    ),
                )
                for item, stock in rows
            )
            warnings = []
            if benchmark_return is None:
                warnings.append("CSI300_BENCHMARK_UNAVAILABLE")
            if unavailable_peer_codes:
                warnings.append(
                    "INDUSTRY_PEER_DATA_PARTIAL_"
                    f"{len(weekly_bars)}_OF_{len(sector_stocks)}"
                )
            if any(
                observation_quality[stock.code] is not DataQuality.VERIFIED
                for _, stock in rows
            ):
                warnings.append("SELECTED_MARKET_DATA_NOT_CROSS_VERIFIED")
            computations.append(
                (
                    compute_weekly_review(
                        week_id=week_id,
                        source_type=decision.type,
                        source_version=decision.version,
                        rule_version=week.rule_version,
                        targets=targets,
                        as_of=review_at,
                        generated_at=generated_at,
                        quality=DataQuality.VERIFIED if not warnings else DataQuality.DEGRADED,
                        benchmark_return=benchmark_return,
                        warnings=tuple(warnings),
                    ),
                    decision.id,
                )
            )

    results = []
    for computed, decision_id in computations:
        async with session_factory() as session:
            results.append(
                await SqlWeeklyReviewApplication(session).persist(
                    computed,
                    decision_set_id=decision_id,
                )
            )
    return results
