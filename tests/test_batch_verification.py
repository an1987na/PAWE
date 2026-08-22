from datetime import UTC, date, datetime
from decimal import Decimal

from pawe_api.contracts import DataQuality
from pawe_api.data.series import NormalizedDailyBar, ProviderDailySeries
from pawe_api.db.models import LegacyItemStaging
from pawe_api.experiments.batch_verification import build_verification_update, stock_key


def _item() -> LegacyItemStaging:
    return LegacyItemStaging(
        id=None,
        document_id=None,
        bucket="main",
        stock_code="300383",
        stock_name="光环新网",
        direction=None,
        rank=1,
        baseline_price=Decimal("17.62"),
        target_return=Decimal("0.11"),
        week_high_return=Decimal("0.1742"),
        close_return=Decimal("0.1742"),
        max_drawdown=Decimal("-0.1039"),
        verification_status="legacy_unverified",
        verification_source=None,
        verified_at=None,
        legacy_recalculated=None,
        v9_recalculated=None,
        verification_warnings=None,
    )


def _series(day_count: int = 5) -> ProviderDailySeries:
    values = [
        (date(2025, 2, 17), "17.45", "18.30", "17.07", "17.77"),
        (date(2025, 2, 18), "17.19", "17.19", "15.79", "16.21"),
        (date(2025, 2, 19), "16.15", "16.90", "16.10", "16.77"),
        (date(2025, 2, 20), "16.52", "17.58", "16.43", "17.23"),
        (date(2025, 2, 21), "18.14", "20.69", "18.06", "20.69"),
    ][:day_count]
    fetched_at = datetime(2026, 8, 3, tzinfo=UTC)
    bars = tuple(
        NormalizedDailyBar(
            stock_key="sz300383",
            trade_date=trade_date,
            open=Decimal(open_price),
            high=Decimal(high),
            low=Decimal(low),
            close=Decimal(close),
            volume=Decimal("100"),
            adjustment="qfq",
            source="tencent",
            fetched_at=fetched_at,
            quality=DataQuality.SINGLE_SOURCE,
        )
        for trade_date, open_price, high, low, close in values
    )
    return ProviderDailySeries("sz300383", "tencent", fetched_at, bars)


def _flat_metric_series() -> ProviderDailySeries:
    fetched_at = datetime(2026, 8, 3, tzinfo=UTC)
    bars = tuple(
        NormalizedDailyBar(
            stock_key="sz300383",
            trade_date=date(2025, 2, day),
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("95"),
            close=Decimal("105"),
            volume=Decimal("100"),
            adjustment="qfq",
            source="tencent",
            fetched_at=fetched_at,
            quality=DataQuality.SINGLE_SOURCE,
        )
        for day in range(17, 22)
    )
    return ProviderDailySeries("sz300383", "tencent", fetched_at, bars)


def test_verification_preserves_legacy_and_v9_baselines_separately() -> None:
    result = build_verification_update(_item(), _series())
    assert result.status == "recalc_single_source"
    assert result.legacy_recalculated is not None
    assert result.v9_recalculated is not None
    assert result.legacy_recalculated["baseline_price"] == 17.62
    assert result.v9_recalculated["entry_price"] == 17.45
    assert result.v9_recalculated["entry_trade_date"] == "2025-02-17"


def test_short_natural_week_is_not_treated_as_complete() -> None:
    result = build_verification_update(_item(), _series(day_count=2))
    assert result.status == "insufficient_week"
    assert result.v9_recalculated is None
    assert result.warnings == ("natural_week_has_only_2_trading_days",)


def test_consistent_metric_shift_is_classified_as_baseline_drift() -> None:
    item = _item()
    item.baseline_price = Decimal("100")
    item.week_high_return = Decimal("0.1033099298")
    item.close_return = Decimal("0.0531594784")
    item.max_drawdown = Decimal("-0.0471414243")
    series = _flat_metric_series()

    result = build_verification_update(item, series)

    assert result.status == "baseline_drift"
    assert result.warnings == ("legacy_baseline_drift_detected",)
    assert result.legacy_recalculated is not None
    drift = result.legacy_recalculated["baseline_drift"]
    assert isinstance(drift, dict)
    assert abs(float(drift["implied_baseline_price"]) - 99.7) < 0.00001


def test_inconsistent_metric_conflict_is_not_baseline_drift() -> None:
    item = _item()
    item.baseline_price = Decimal("100")
    item.week_high_return = Decimal("0.20")
    item.close_return = Decimal("0.05")
    item.max_drawdown = Decimal("-0.05")
    series = _flat_metric_series()

    result = build_verification_update(item, series)

    assert result.status == "conflicted"
    assert result.legacy_recalculated is not None
    assert "baseline_drift" not in result.legacy_recalculated


def test_stock_key_mapping_is_explicit() -> None:
    assert stock_key("300383") == "sz300383"
    assert stock_key("000977") == "sz000977"
    assert stock_key("600519") == "sh600519"
