from datetime import date, timedelta

from pawe_api.contracts import DataQuality
from pawe_api.data.calendar import (
    TradingCalendarObservation,
    TradingWeekStatus,
    assess_trading_week,
    reconcile_trading_calendar,
)

WEEK_ID = date(2026, 8, 3)


def _observation(source: str, offsets: tuple[int, ...]) -> TradingCalendarObservation:
    return TradingCalendarObservation(
        source=source,
        week_id=WEEK_ID,
        open_dates=tuple(WEEK_ID + timedelta(days=offset) for offset in offsets),
        quality=DataQuality.SINGLE_SOURCE,
    )


def test_matching_calendar_sources_are_verified() -> None:
    days = reconcile_trading_calendar(
        WEEK_ID,
        _observation("exchange", (1, 2, 3, 4)),
        _observation("eastmoney", (1, 2, 3, 4)),
    )
    assert {day.quality for day in days} == {DataQuality.VERIFIED}
    assessment = assess_trading_week(WEEK_ID, days)
    assert assessment.status is TradingWeekStatus.ELIGIBLE
    assert assessment.data_quality is DataQuality.VERIFIED
    assert assessment.trading_day_count == 4


def test_calendar_source_conflict_blocks_week() -> None:
    days = reconcile_trading_calendar(
        WEEK_ID,
        _observation("exchange", (0, 1, 2, 3, 4)),
        _observation("eastmoney", (1, 2, 3, 4)),
    )
    assert {day.quality for day in days} == {DataQuality.CONFLICTED}
    assessment = assess_trading_week(WEEK_ID, days)
    assert assessment.status is TradingWeekStatus.DATA_DEGRADED
    assert assessment.qualifies is False


def test_backup_only_calendar_is_degraded_but_can_be_manually_assessed() -> None:
    days = reconcile_trading_calendar(
        WEEK_ID,
        None,
        _observation("eastmoney", (1, 2, 3, 4)),
    )
    assert {day.quality for day in days} == {DataQuality.DEGRADED}
    assessment = assess_trading_week(WEEK_ID, days)
    assert assessment.qualifies is True
    assert assessment.data_quality is DataQuality.DEGRADED


def test_all_calendar_sources_missing_block_week() -> None:
    days = reconcile_trading_calendar(WEEK_ID, None, None)
    assert assess_trading_week(WEEK_ID, days).status is TradingWeekStatus.DATA_DEGRADED
