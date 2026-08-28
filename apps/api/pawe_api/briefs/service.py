from dataclasses import dataclass

from pawe_api.contracts import DailyBriefItem, DailyRiskStatus, DataQuality

TARGET_RETURN = 0.10


@dataclass(frozen=True, slots=True)
class PublishedTarget:
    stock_code: str
    stock_name: str
    monday_open: float


@dataclass(frozen=True, slots=True)
class DailyMarketSnapshot:
    previous_close: float
    close: float
    week_high: float
    volume: float
    previous_five_day_average_volume: float | None
    quality: DataQuality
    invalidation_triggered: bool = False


def build_deterministic_brief_item(
    target: PublishedTarget,
    market: DailyMarketSnapshot,
) -> DailyBriefItem:
    _validate_positive_prices(target, market)
    daily_return = market.close / market.previous_close - 1
    week_to_date_return = market.close / target.monday_open - 1
    week_high_return = market.week_high / target.monday_open - 1
    drawdown = market.close / market.week_high - 1
    volume_activity = _volume_activity(market)
    risk_status = _risk_status(
        quality=market.quality,
        week_to_date_return=week_to_date_return,
        week_high_return=week_high_return,
        drawdown=drawdown,
        volume_activity=volume_activity,
        invalidation_triggered=market.invalidation_triggered,
    )

    volume_summary = (
        "量能数据不足"
        if volume_activity is None
        else f"成交量为前5日均量的{volume_activity:.2f}倍"
    )
    summary = f"当日收盘较前收{daily_return:+.2%}；{volume_summary}。"
    return DailyBriefItem(
        stock_code=target.stock_code,
        stock_name=target.stock_name,
        daily_return=daily_return,
        week_to_date_return=week_to_date_return,
        week_high_return=week_high_return,
        drawdown_from_week_high=drawdown,
        distance_to_target=max(0.0, TARGET_RETURN - week_high_return),
        volume_activity=volume_activity,
        risk_status=risk_status,
        summary=summary,
    )


def _validate_positive_prices(
    target: PublishedTarget,
    market: DailyMarketSnapshot,
) -> None:
    values = (target.monday_open, market.previous_close, market.close, market.week_high)
    if any(value <= 0 for value in values):
        raise ValueError("price inputs must be positive")
    if market.week_high < market.close:
        raise ValueError("week_high cannot be lower than close")


def _volume_activity(market: DailyMarketSnapshot) -> float | None:
    average = market.previous_five_day_average_volume
    if average is None or average <= 0:
        return None
    return market.volume / average


def _risk_status(
    *,
    quality: DataQuality,
    week_to_date_return: float,
    week_high_return: float,
    drawdown: float,
    volume_activity: float | None,
    invalidation_triggered: bool,
) -> DailyRiskStatus:
    if quality not in {DataQuality.VERIFIED, DataQuality.SINGLE_SOURCE}:
        return DailyRiskStatus.DATA_DEGRADED
    if invalidation_triggered or week_to_date_return <= -0.08 or drawdown <= -0.08:
        return DailyRiskStatus.RISK_TRIGGERED
    if week_high_return >= TARGET_RETURN:
        return DailyRiskStatus.ON_TRACK
    if week_to_date_return >= 0.05 and volume_activity is not None and volume_activity >= 1:
        return DailyRiskStatus.ON_TRACK
    return DailyRiskStatus.WATCH
