import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date


class FeatureCalculationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DailyBarInput:
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    adjustment: str = "qfq"


@dataclass(frozen=True, slots=True)
class TechnicalFeatures:
    as_of: date
    return_5d: float
    return_20d: float
    return_60d: float
    distance_high_20d: float
    volume_activity_5d: float
    avg_amount_20d: float
    volatility_20d: float
    above_ma20: bool
    amount_anomaly_days: int


def calculate_technical_features(
    bars: Sequence[DailyBarInput],
    *,
    as_of: date,
) -> TechnicalFeatures:
    eligible = [bar for bar in bars if bar.trade_date <= as_of]
    if len(eligible) < 61:
        raise FeatureCalculationError("at least 61 completed trading days are required")
    if any(
        left.trade_date >= right.trade_date
        for left, right in zip(eligible, eligible[1:], strict=False)
    ):
        raise FeatureCalculationError("daily bars must be strictly ordered with unique dates")
    if any(bar.adjustment != "qfq" for bar in eligible):
        raise FeatureCalculationError("all technical feature bars must use qfq adjustment")
    if any(
        min(bar.open, bar.high, bar.low, bar.close) <= 0
        or bar.volume < 0
        or bar.amount < 0
        or bar.high < max(bar.open, bar.close, bar.low)
        or bar.low > min(bar.open, bar.close, bar.high)
        for bar in eligible
    ):
        raise FeatureCalculationError("daily bars contain invalid OHLCV values")

    current = eligible[-1]
    closes = [bar.close for bar in eligible]
    last_20 = eligible[-20:]
    last_5 = eligible[-5:]
    previous_5 = eligible[-10:-5]
    previous_volume_average = sum(bar.volume for bar in previous_5) / 5
    if previous_volume_average <= 0:
        raise FeatureCalculationError("previous five-day average volume must be positive")

    daily_returns = [
        right.close / left.close - 1
        for left, right in zip(eligible[-21:-1], eligible[-20:], strict=True)
    ]
    median_amount = statistics.median(bar.amount for bar in last_20)
    amount_anomaly_days = (
        sum(bar.amount > median_amount * 5 for bar in last_20)
        if median_amount > 0
        else 0
    )
    return TechnicalFeatures(
        as_of=current.trade_date,
        return_5d=current.close / closes[-6] - 1,
        return_20d=current.close / closes[-21] - 1,
        return_60d=current.close / closes[-61] - 1,
        distance_high_20d=current.close / max(bar.high for bar in last_20) - 1,
        volume_activity_5d=(sum(bar.volume for bar in last_5) / 5) / previous_volume_average,
        avg_amount_20d=sum(bar.amount for bar in last_20) / 20,
        volatility_20d=_population_stddev(daily_returns),
        above_ma20=current.close > sum(bar.close for bar in last_20) / 20,
        amount_anomaly_days=amount_anomaly_days,
    )


def _population_stddev(values: Sequence[float]) -> float:
    if not values:
        raise FeatureCalculationError("volatility requires daily returns")
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))
