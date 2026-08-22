from dataclasses import dataclass
from datetime import date


class WeeklyEvaluationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WeeklyBar:
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    suspended_at_open: bool = False
    limit_up_at_open: bool = False


@dataclass(frozen=True, slots=True)
class WeeklyPerformance:
    entry_trade_date: date
    entry_price: float
    accessible_at_entry: bool
    accessibility_reason: str | None
    week_high_return: float
    week_close_return: float
    max_drawdown_from_entry: float
    max_peak_to_trough_drawdown: float
    target_touched: bool
    near_target: bool
    target_touch_date: date | None
    drawdown_before_touch: float | None
    touch_intraday_order_unknown: bool


def evaluate_weekly_path(
    bars: list[WeeklyBar],
    *,
    target_return: float = 0.10,
    near_target_return: float = 0.08,
) -> WeeklyPerformance:
    if target_return <= 0 or not 0 <= near_target_return < target_return:
        raise WeeklyEvaluationError("target thresholds are invalid")
    if not bars:
        raise WeeklyEvaluationError("weekly evaluation requires at least one trading day")
    if any(
        left.trade_date >= right.trade_date for left, right in zip(bars, bars[1:], strict=False)
    ):
        raise WeeklyEvaluationError("weekly bars must be strictly ordered")
    if any(
        min(bar.open, bar.high, bar.low, bar.close) <= 0
        or bar.high < max(bar.open, bar.close, bar.low)
        or bar.low > min(bar.open, bar.close, bar.high)
        for bar in bars
    ):
        raise WeeklyEvaluationError("weekly bars contain invalid OHLC values")

    first = bars[0]
    entry_price = first.open
    accessibility_reason = _accessibility_reason(first)
    high_returns = [bar.high / entry_price - 1 for bar in bars]
    week_high_return = max(high_returns)
    target_touch_index = next(
        (index for index, value in enumerate(high_returns) if value >= target_return),
        None,
    )
    peak = entry_price
    max_peak_drawdown = 0.0
    for bar in bars:
        peak = max(peak, bar.high)
        max_peak_drawdown = min(max_peak_drawdown, bar.low / peak - 1)

    drawdown_before_touch = None
    touch_date = None
    if target_touch_index is not None:
        touch_date = bars[target_touch_index].trade_date
        drawdown_before_touch = min(
            bar.low / entry_price - 1 for bar in bars[: target_touch_index + 1]
        )
    return WeeklyPerformance(
        entry_trade_date=first.trade_date,
        entry_price=entry_price,
        accessible_at_entry=accessibility_reason is None,
        accessibility_reason=accessibility_reason,
        week_high_return=week_high_return,
        week_close_return=bars[-1].close / entry_price - 1,
        max_drawdown_from_entry=min(bar.low / entry_price - 1 for bar in bars),
        max_peak_to_trough_drawdown=max_peak_drawdown,
        target_touched=target_touch_index is not None,
        near_target=near_target_return <= week_high_return < target_return,
        target_touch_date=touch_date,
        drawdown_before_touch=drawdown_before_touch,
        touch_intraday_order_unknown=target_touch_index is not None,
    )


def _accessibility_reason(first: WeeklyBar) -> str | None:
    if first.suspended_at_open:
        return "suspended_at_entry"
    if first.limit_up_at_open:
        return "limit_up_at_entry"
    return None
