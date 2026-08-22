from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from pawe_api.contracts import DataQuality
from pawe_api.data.series import NormalizedDailyBar, ProviderDailySeries
from pawe_api.db import models
from pawe_api.features.market_snapshot import (
    _prefer_complete_backup,
    build_technical_observation,
)


def _series(source: str, *, amount: bool) -> ProviderDailySeries:
    fetched_at = datetime(2026, 8, 9, 8, tzinfo=UTC)
    start = date(2026, 5, 1)
    bars = tuple(
        NormalizedDailyBar(
            stock_key="sh600519",
            trade_date=start + timedelta(days=index),
            open=Decimal(100 + index),
            high=Decimal(102 + index),
            low=Decimal(99 + index),
            close=Decimal(101 + index),
            volume=Decimal(1000 + index),
            amount=Decimal(100000000 + index) if amount else None,
            adjustment="qfq",
            source=source,
            fetched_at=fetched_at,
            quality=DataQuality.SINGLE_SOURCE,
        )
        for index in range(61)
    )
    return ProviderDailySeries("sh600519", source, fetched_at, bars)


def test_builds_replayable_verified_technical_observation() -> None:
    stock = models.Stock(
        id=1,
        code="600519",
        exchange="SSE",
        board="main",
        name="贵州茅台",
        listing_date=date(2001, 8, 27),
        status="active",
    )
    result = build_technical_observation(
        stock,
        _series("tencent", amount=False),
        _series("eastmoney", amount=True),
        as_of=date(2026, 6, 30),
    )

    assert result.quality is DataQuality.VERIFIED
    assert result.features.avg_amount_20d > 100_000_000
    assert result.payload["schema_version"] == "technical-market-2"
    assert set(result.payload["source_bars"]) == {"tencent", "eastmoney"}


def test_uses_tencent_prices_and_sina_amount_under_explicit_degradation() -> None:
    stock = models.Stock(
        id=2,
        code="600011",
        exchange="SSE",
        board="main",
        name="华能国际",
        listing_date=date(2001, 12, 6),
        status="active",
    )
    sina = _series("sina", amount=True)
    sina = ProviderDailySeries(
        sina.stock_key,
        sina.source,
        sina.fetched_at,
        tuple(replace(bar, close=bar.close * Decimal("0.99"), source="sina") for bar in sina.bars),
    )

    result = build_technical_observation(
        stock,
        _series("tencent", amount=False),
        sina,
        as_of=date(2026, 6, 30),
    )

    assert result.quality is DataQuality.SINGLE_SOURCE
    verification = result.payload["verification"]
    assert isinstance(verification, dict)
    assert "amount_fallback:sina" in verification["warnings"]


def test_complete_backup_precedes_preferred_but_partial_source() -> None:
    eastmoney = _series("eastmoney", amount=True)
    eastmoney = replace(eastmoney, bars=eastmoney.bars[-11:])
    sina = _series("sina", amount=True)

    ordered = _prefer_complete_backup([eastmoney, sina])

    assert [series.source for series in ordered] == ["sina", "eastmoney"]
