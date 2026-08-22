from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from pawe_api.contracts import DataQuality
from pawe_api.data.series import (
    NormalizedDailyBar,
    ProviderDailySeries,
    merge_provider_daily_series,
    reconcile_daily_series,
    reconcile_daily_series_with_amount_fallback,
)

from scripts.ingest_daily_bars import _requires_sina_fallback

SHANGHAI = ZoneInfo("Asia/Shanghai")
FETCHED_AT = datetime(2026, 8, 3, 18, tzinfo=SHANGHAI)


def _bar(source: str, close: str = "10.00") -> NormalizedDailyBar:
    return NormalizedDailyBar(
        stock_key="sz000001",
        trade_date=date(2026, 7, 31),
        open=Decimal("9.90"),
        high=Decimal("10.10"),
        low=Decimal("9.80"),
        close=Decimal(close),
        volume=Decimal("1000000"),
        amount=Decimal("10000000"),
        adjustment="qfq",
        source=source,
        fetched_at=FETCHED_AT,
        quality=DataQuality.SINGLE_SOURCE,
    )


def _series(source: str, close: str = "10.00") -> ProviderDailySeries:
    return ProviderDailySeries(
        stock_key="sz000001",
        source=source,
        fetched_at=FETCHED_AT,
        bars=(_bar(source, close),),
    )


def test_matching_primary_and_backup_are_verified() -> None:
    result = reconcile_daily_series(_series("tencent"), _series("eastmoney", "10.01"))
    assert result.quality is DataQuality.VERIFIED
    assert result.sources == ("tencent", "eastmoney")
    assert result.bars[0].quality is DataQuality.VERIFIED


def test_partial_backup_requests_second_backup_and_merges_complementary_dates() -> None:
    primary = replace(
        _series("tencent"),
        bars=(
            _bar("tencent"),
            replace(_bar("tencent"), trade_date=date(2026, 8, 3)),
        ),
    )
    eastmoney = _series("eastmoney")
    sina = replace(
        _series("sina"),
        bars=(replace(_bar("sina"), trade_date=date(2026, 8, 3)),),
    )

    assert _requires_sina_fallback(primary, eastmoney)
    merged = merge_provider_daily_series((eastmoney, sina))
    assert merged is not None
    assert [bar.trade_date for bar in merged.bars] == [
        date(2026, 7, 31),
        date(2026, 8, 3),
    ]
    assert "backup_sources_merged" in merged.warnings
    result = reconcile_daily_series(primary, merged)
    assert result.quality is DataQuality.VERIFIED
    assert result.sources == ("tencent", "eastmoney+sina")


def test_redundant_lower_priority_backup_is_not_labeled_as_a_contributor() -> None:
    full = replace(
        _series("sina"),
        bars=(
            _bar("sina"),
            replace(_bar("sina"), trade_date=date(2026, 8, 3)),
        ),
    )
    subset = _series("eastmoney")

    merged = merge_provider_daily_series((full, subset))

    assert merged is not None
    assert merged.source == "sina"
    assert "backup_sources_merged" not in merged.warnings


def test_price_conflict_blocks_bars() -> None:
    result = reconcile_daily_series(_series("tencent"), _series("eastmoney", "10.20"))
    assert result.quality is DataQuality.CONFLICTED
    assert result.bars == ()
    assert result.warnings[0].startswith("daily_source_conflict:")


def test_backup_only_is_explicitly_degraded() -> None:
    result = reconcile_daily_series(None, _series("eastmoney"))
    assert result.quality is DataQuality.DEGRADED
    assert result.warnings == ("primary_daily_source_missing",)
    assert result.bars[0].quality is DataQuality.DEGRADED


def test_all_sources_missing_is_not_silently_zero_filled() -> None:
    result = reconcile_daily_series(None, None)
    assert result.quality is DataQuality.MISSING
    assert result.bars == ()


def test_delayed_source_downgrades_matching_series() -> None:
    delayed = replace(_series("eastmoney"), is_delayed=True)
    result = reconcile_daily_series(_series("tencent"), delayed)
    assert result.quality is DataQuality.DEGRADED
    assert "daily_source_delayed" in result.warnings


def test_invalid_tolerance_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        reconcile_daily_series(
            _series("tencent"),
            _series("eastmoney"),
            price_tolerance=Decimal("-0.01"),
        )


def test_sina_can_supply_amount_when_only_adjusted_prices_conflict() -> None:
    primary = _series("tencent")
    sina = _series("sina", "10.20")

    result = reconcile_daily_series_with_amount_fallback(primary, sina)

    assert result.quality is DataQuality.SINGLE_SOURCE
    assert result.bars[0].close == Decimal("10.00")
    assert result.bars[0].amount == Decimal("10000000")
    assert "amount_fallback:sina" in result.warnings
    assert "price_verification_unavailable:qfq_factor_conflict" in result.warnings


def test_sina_amount_fallback_rejects_a_real_volume_conflict() -> None:
    primary = _series("tencent")
    conflicting_bar = replace(_bar("sina", "10.20"), volume=Decimal("900000"))
    sina = replace(_series("sina", "10.20"), bars=(conflicting_bar,))

    result = reconcile_daily_series_with_amount_fallback(primary, sina)

    assert result.quality is DataQuality.CONFLICTED
    assert result.bars == ()


def test_partial_backup_supplies_history_while_close_quote_supplies_today_amount() -> None:
    historical_primary = replace(_bar("tencent"), amount=None)
    today_primary = replace(
        _bar("tencent", "10.20"),
        trade_date=date(2026, 8, 3),
        amount=Decimal("12000000"),
    )
    primary = replace(
        _series("tencent"),
        bars=(historical_primary, today_primary),
    )
    backup = _series("eastmoney")

    result = reconcile_daily_series_with_amount_fallback(primary, backup)

    assert result.quality is DataQuality.SINGLE_SOURCE
    assert [bar.amount for bar in result.bars] == [
        Decimal("10000000"),
        Decimal("12000000"),
    ]
    assert "amount_fallback:eastmoney" in result.warnings
