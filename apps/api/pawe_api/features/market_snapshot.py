from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pawe_api.contracts import DataQuality
from pawe_api.data.series import (
    NormalizedDailyBar,
    ProviderDailySeries,
    merge_provider_daily_series,
    reconcile_daily_series,
    reconcile_daily_series_with_amount_fallback,
)
from pawe_api.db import models
from pawe_api.features.technical import (
    DailyBarInput,
    FeatureCalculationError,
    TechnicalFeatures,
    calculate_technical_features,
)

TECHNICAL_SNAPSHOT_SCHEMA_VERSION = "technical-market-2"


@dataclass(frozen=True, slots=True)
class TechnicalSnapshotObservation:
    stock_id: int
    stock_code: str
    as_of: date
    fetched_at: datetime
    quality: DataQuality
    features: TechnicalFeatures
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class DailyBriefObservation:
    quality: DataQuality
    payload: dict[str, object]


async def build_stored_daily_brief_observation(
    session: AsyncSession,
    stock: models.Stock,
    *,
    as_of: date,
    snapshot_cutoff: datetime,
) -> DailyBriefObservation:
    """Read close data for a brief without imposing weekly-feature amount requirements."""
    stock_key = ("sh" if stock.exchange == "SSE" else "sz") + stock.code
    primary = await _load_visible_series(
        session,
        stock_id=stock.id,
        stock_key=stock_key,
        source="tencent",
        as_of=as_of,
        snapshot_cutoff=snapshot_cutoff,
    )
    backups = [
        candidate
        for source in ("eastmoney", "sina")
        if (
            candidate := await _load_visible_series(
                session,
                stock_id=stock.id,
                stock_key=stock_key,
                source=source,
                as_of=as_of,
                snapshot_cutoff=snapshot_cutoff,
            )
        )
        is not None
    ]
    backup = merge_provider_daily_series(_prefer_complete_backup(backups))
    reconciled = reconcile_daily_series(primary, backup)
    source_series = tuple(series for series in (primary, backup) if series is not None)
    if reconciled.quality in {DataQuality.CONFLICTED, DataQuality.MISSING}:
        if primary is None:
            raise FeatureCalculationError("daily close source is unavailable for the brief")
        reconciled = reconcile_daily_series(primary, None)
        source_series = (primary,)
    if not reconciled.bars:
        raise FeatureCalculationError("daily close bars are unavailable for the brief")
    return DailyBriefObservation(
        quality=reconciled.quality,
        payload={
            "verification": {
                "quality": reconciled.quality.value,
                "sources": list(reconciled.sources),
                "warnings": list(reconciled.warnings),
            },
            "source_bars": {
                series.source: [_bar_payload(bar) for bar in series.bars[-61:]]
                for series in source_series
            },
        },
    )


async def build_stored_technical_observation(
    session: AsyncSession,
    stock: models.Stock,
    *,
    as_of: date,
    snapshot_cutoff: datetime,
) -> TechnicalSnapshotObservation:
    stock_key = ("sh" if stock.exchange == "SSE" else "sz") + stock.code
    primary = await _load_visible_series(
        session,
        stock_id=stock.id,
        stock_key=stock_key,
        source="tencent",
        as_of=as_of,
        snapshot_cutoff=snapshot_cutoff,
    )
    backup_candidates: list[ProviderDailySeries] = []
    for source in ("eastmoney", "sina"):
        candidate = await _load_visible_series(
            session,
            stock_id=stock.id,
            stock_key=stock_key,
            source=source,
            as_of=as_of,
            snapshot_cutoff=snapshot_cutoff,
        )
        if candidate is not None:
            backup_candidates.append(candidate)
    backup = merge_provider_daily_series(_prefer_complete_backup(backup_candidates))
    return build_technical_observation(stock, primary, backup, as_of=as_of)


async def build_retrospective_technical_observation(
    session: AsyncSession,
    stock: models.Stock,
    *,
    as_of: date,
    retrieved_by: datetime,
) -> TechnicalSnapshotObservation:
    """Build a research replay observation without backdating the actual fetch time.

    Information is bounded by ``as_of`` while provider versions are bounded by the
    real ``retrieved_by`` time. Callers must label the result as retrospective.
    """
    stock_key = ("sh" if stock.exchange == "SSE" else "sz") + stock.code
    primary = await _load_visible_series(
        session,
        stock_id=stock.id,
        stock_key=stock_key,
        source="tencent",
        as_of=as_of,
        snapshot_cutoff=retrieved_by,
    )
    backup_candidates: list[ProviderDailySeries] = []
    for source in ("eastmoney", "sina"):
        candidate = await _load_visible_series(
            session,
            stock_id=stock.id,
            stock_key=stock_key,
            source=source,
            as_of=as_of,
            snapshot_cutoff=retrieved_by,
        )
        if candidate is not None:
            backup_candidates.append(candidate)
    backup = max(
        backup_candidates,
        key=lambda series: (len(series.bars), series.source == "eastmoney"),
        default=None,
    )
    return build_technical_observation(stock, primary, backup, as_of=as_of)


def build_technical_observation(
    stock: models.Stock,
    primary: ProviderDailySeries | None,
    backup: ProviderDailySeries | None,
    *,
    as_of: date,
) -> TechnicalSnapshotObservation:
    # V9 technical features use exactly the latest 61 completed sessions. Keep
    # source verification on that same information window so older, unused qfq
    # factors or missing amounts cannot invalidate an otherwise complete input.
    primary = _tail_series(primary, 61)
    if primary is None:
        backup = _tail_series(backup, 61)
    elif backup is not None:
        feature_dates = {bar.trade_date for bar in primary.bars}
        aligned_backup_bars = tuple(
            bar for bar in backup.bars if bar.trade_date in feature_dates
        )
        backup = (
            replace(backup, bars=aligned_backup_bars)
            if aligned_backup_bars
            else None
        )
    reconciled = reconcile_daily_series_with_amount_fallback(primary, backup)
    if reconciled.quality in {DataQuality.CONFLICTED, DataQuality.MISSING}:
        raise FeatureCalculationError("daily sources are not usable for a snapshot")
    if backup is None or not reconciled.bars:
        raise FeatureCalculationError("an amount-bearing daily source is required")
    feature_bars = [
        DailyBarInput(
            trade_date=bar.trade_date,
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=float(bar.volume),
            amount=float(_required_amount(bar.amount)),
            adjustment=bar.adjustment,
        )
        for bar in reconciled.bars
    ]
    features = calculate_technical_features(feature_bars, as_of=as_of)
    source_series = tuple(series for series in (primary, backup) if series is not None)
    fetched_at = max(series.fetched_at for series in source_series)
    payload: dict[str, object] = {
        "schema_version": TECHNICAL_SNAPSHOT_SCHEMA_VERSION,
        "stock": {
            "code": stock.code,
            "name": stock.name,
            "exchange": stock.exchange,
            "board": stock.board,
        },
        "technical": _technical_payload(features),
        "verification": {
            "quality": reconciled.quality.value,
            "sources": list(reconciled.sources),
            "warnings": list(reconciled.warnings),
        },
        "source_bars": {
            series.source: [_bar_payload(bar) for bar in series.bars[-61:]]
            for series in source_series
        },
    }
    return TechnicalSnapshotObservation(
        stock_id=stock.id,
        stock_code=stock.code,
        as_of=features.as_of,
        fetched_at=fetched_at,
        quality=reconciled.quality,
        features=features,
        payload=payload,
    )


def _tail_series(
    series: ProviderDailySeries | None, count: int
) -> ProviderDailySeries | None:
    if series is None or len(series.bars) <= count:
        return series
    return replace(series, bars=series.bars[-count:])


async def _load_visible_series(
    session: AsyncSession,
    *,
    stock_id: int,
    stock_key: str,
    source: str,
    as_of: date,
    snapshot_cutoff: datetime,
) -> ProviderDailySeries | None:
    ranked = (
        select(
            models.DailyBar,
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
    rows = list(
        (
            await session.execute(
                select(ranked).where(ranked.c.version_rank == 1).order_by(ranked.c.trade_date)
            )
        ).mappings()
    )
    if not rows:
        return None
    bars = tuple(
        NormalizedDailyBar(
            stock_key=stock_key,
            trade_date=row.trade_date,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
            amount=row.amount,
            adjustment=row.adjustment,
            source=row.source,
            fetched_at=row.fetched_at,
            quality=DataQuality(row.quality),
        )
        for row in rows
    )
    return ProviderDailySeries(
        stock_key=stock_key,
        source=source,
        fetched_at=max(bar.fetched_at for bar in bars),
        bars=bars,
    )


def _required_amount(value: Decimal | None) -> Decimal:
    if value is None:
        raise FeatureCalculationError("daily amount is required")
    return value


def _prefer_complete_backup(
    candidates: list[ProviderDailySeries],
) -> list[ProviderDailySeries]:
    """Prefer the freshest coverage, then width, with Eastmoney as the tie-breaker.

    A stale series can be slightly wider because it was fetched from an earlier
    window.  Letting that width win would discard a current backup and can
    introduce obsolete qfq-factor conflicts into an otherwise valid snapshot.
    """
    ordered = sorted(
        candidates,
        key=lambda series: (
            series.bars[-1].trade_date if series.bars else date.min,
            len(series.bars),
            series.source == "eastmoney",
        ),
        reverse=True,
    )
    if not ordered or len(ordered[0].bars) < 61:
        return ordered
    latest_date = ordered[0].bars[-1].trade_date
    return [series for series in ordered if series.bars[-1].trade_date == latest_date]


def _technical_payload(features: TechnicalFeatures) -> dict[str, object]:
    payload = asdict(features)
    payload["as_of"] = features.as_of.isoformat()
    return payload


def _bar_payload(bar: NormalizedDailyBar) -> dict[str, object]:
    return {
        "trade_date": bar.trade_date.isoformat(),
        "open": format(bar.open, "f"),
        "high": format(bar.high, "f"),
        "low": format(bar.low, "f"),
        "close": format(bar.close, "f"),
        "volume": format(bar.volume, "f"),
        "amount": None if bar.amount is None else format(bar.amount, "f"),
        "adjustment": bar.adjustment,
        "fetched_at": bar.fetched_at.isoformat(),
    }
