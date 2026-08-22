from dataclasses import dataclass

from pawe_api.evaluation.weekly import WeeklyBar
from pawe_api.experiments.legacy import LegacyItem


@dataclass(frozen=True, slots=True)
class MetricVerification:
    metric: str
    claimed: float
    recalculated: float
    absolute_delta: float
    within_tolerance: bool


@dataclass(frozen=True, slots=True)
class LegacyItemVerification:
    stock_code: str
    status: str
    metrics: tuple[MetricVerification, ...]


def verify_legacy_item_metrics(
    item: LegacyItem,
    bars: list[WeeklyBar],
    *,
    tolerance: float = 0.00015,
) -> LegacyItemVerification:
    if tolerance < 0:
        raise ValueError("verification tolerance cannot be negative")
    if item.baseline_price is None or not bars:
        return LegacyItemVerification(item.stock_code, "insufficient_data", ())
    baseline = item.baseline_price
    recalculated = {
        "week_high_return": max(bar.high for bar in bars) / baseline - 1,
        "close_return": bars[-1].close / baseline - 1,
        "max_drawdown": min(bar.low for bar in bars) / baseline - 1,
    }
    claimed = {
        "week_high_return": item.week_high_return,
        "close_return": item.close_return,
        "max_drawdown": item.max_drawdown,
    }
    metrics: list[MetricVerification] = []
    for metric, claimed_value in claimed.items():
        if claimed_value is None:
            continue
        delta = abs(claimed_value - recalculated[metric])
        metrics.append(
            MetricVerification(
                metric=metric,
                claimed=claimed_value,
                recalculated=recalculated[metric],
                absolute_delta=delta,
                within_tolerance=delta <= tolerance,
            )
        )
    if not metrics:
        status = "insufficient_data"
    elif all(metric.within_tolerance for metric in metrics):
        status = "verified"
    else:
        status = "conflicted"
    return LegacyItemVerification(item.stock_code, status, tuple(metrics))
