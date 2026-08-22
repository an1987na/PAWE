from datetime import date, timedelta

import pytest
from pawe_api.features.technical import (
    DailyBarInput,
    FeatureCalculationError,
    calculate_technical_features,
)


def _bars(count: int = 61) -> list[DailyBarInput]:
    start = date(2026, 5, 1)
    bars = []
    for index in range(count):
        close = 100 + index
        bars.append(
            DailyBarInput(
                trade_date=start + timedelta(days=index),
                open=close - 0.5,
                high=close + 1,
                low=close - 1,
                close=close,
                volume=1000 + index * 10,
                amount=200_000_000 + index * 1_000_000,
            )
        )
    return bars


def test_calculates_fixed_window_features_without_future_rows() -> None:
    bars = _bars(62)
    as_of = bars[-2].trade_date
    features = calculate_technical_features(bars, as_of=as_of)
    assert features.as_of == as_of
    assert features.return_5d == pytest.approx(160 / 155 - 1)
    assert features.return_20d == pytest.approx(160 / 140 - 1)
    assert features.return_60d == pytest.approx(160 / 100 - 1)
    assert features.avg_amount_20d > 200_000_000
    assert features.volatility_20d >= 0
    assert features.amount_anomaly_days == 0


def test_counts_twenty_day_amount_outliers_against_the_window_median() -> None:
    bars = _bars()
    bars[-1] = DailyBarInput(
        trade_date=bars[-1].trade_date,
        open=159.5,
        high=161,
        low=159,
        close=160,
        volume=1600,
        amount=2_000_000_000,
    )

    features = calculate_technical_features(bars, as_of=bars[-1].trade_date)

    assert features.amount_anomaly_days == 1


def test_requires_sixty_day_lookback_plus_current_day() -> None:
    with pytest.raises(FeatureCalculationError, match="61"):
        calculate_technical_features(_bars(60), as_of=date(2026, 8, 1))


def test_rejects_mixed_adjustment_sequences() -> None:
    bars = _bars()
    bars[-1] = DailyBarInput(
        trade_date=bars[-1].trade_date,
        open=159.5,
        high=161,
        low=159,
        close=160,
        volume=1600,
        amount=260_000_000,
        adjustment="raw",
    )
    with pytest.raises(FeatureCalculationError, match="qfq"):
        calculate_technical_features(bars, as_of=bars[-1].trade_date)
