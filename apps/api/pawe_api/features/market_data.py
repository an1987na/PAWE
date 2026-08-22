from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pawe_api.db import models
from pawe_api.features.technical import (
    DailyBarInput,
    FeatureCalculationError,
    TechnicalFeatures,
    calculate_technical_features,
)


async def calculate_stored_technical_features(
    session: AsyncSession,
    *,
    stock_id: int,
    as_of: date,
    snapshot_cutoff: datetime,
    source: str = "eastmoney",
) -> TechnicalFeatures:
    """Build features only from versions fetched by the snapshot cutoff."""
    ranked = (
        select(
            models.DailyBar.trade_date,
            models.DailyBar.open,
            models.DailyBar.high,
            models.DailyBar.low,
            models.DailyBar.close,
            models.DailyBar.volume,
            models.DailyBar.amount,
            models.DailyBar.adjustment,
            func.row_number()
            .over(
                partition_by=models.DailyBar.trade_date,
                order_by=(models.DailyBar.fetched_at.desc(), models.DailyBar.id.desc()),
            )
            .label("version_rank"),
        )
        .where(
            models.DailyBar.stock_id == stock_id,
            models.DailyBar.trade_date <= as_of,
            models.DailyBar.fetched_at <= snapshot_cutoff,
            models.DailyBar.adjustment == "qfq",
            models.DailyBar.source == source,
        )
        .subquery()
    )
    rows = (
        await session.execute(
            select(ranked)
            .where(ranked.c.version_rank == 1)
            .order_by(ranked.c.trade_date)
        )
    ).mappings()
    bars: list[DailyBarInput] = []
    for row in rows:
        if row.amount is None:
            raise FeatureCalculationError(
                f"{source} daily amount is required for technical features"
            )
        bars.append(
            DailyBarInput(
                trade_date=row.trade_date,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
                amount=float(row.amount),
                adjustment=row.adjustment,
            )
        )
    return calculate_technical_features(bars, as_of=as_of)
