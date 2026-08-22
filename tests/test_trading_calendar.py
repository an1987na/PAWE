from datetime import date, timedelta

import pytest
from pawe_api.contracts import DataQuality
from pawe_api.data.calendar import (
    TradingCalendarDay,
    TradingCalendarError,
    TradingWeekStatus,
    assess_trading_week,
    build_trading_week_schedule,
    is_weekly_preopen_run_date,
)

WEEK_ID = date(2026, 8, 3)


def _calendar(open_offsets: set[int]) -> list[TradingCalendarDay]:
    return [
        TradingCalendarDay(
            calendar_date=WEEK_ID + timedelta(days=offset),
            is_open=offset in open_offsets,
            quality=DataQuality.VERIFIED,
        )
        for offset in range(5)
    ]


@pytest.mark.parametrize(
    "open_offsets",
    [{0, 1, 2}, {1, 2, 3}, {2, 3, 4}, {0, 1, 2, 3, 4}],
)
def test_three_or_more_trading_days_qualify(open_offsets: set[int]) -> None:
    result = assess_trading_week(WEEK_ID, _calendar(open_offsets))
    assert result.qualifies is True
    assert result.status is TradingWeekStatus.ELIGIBLE
    assert result.longest_consecutive_run >= 3


def test_four_open_days_split_by_holiday_still_qualify() -> None:
    result = assess_trading_week(WEEK_ID, _calendar({0, 1, 3, 4}))
    assert result.qualifies is True
    assert result.status is TradingWeekStatus.ELIGIBLE
    assert result.trading_day_count == 4
    assert result.longest_consecutive_run == 2
    assert result.reason is None


def test_two_trading_days_do_not_qualify() -> None:
    result = assess_trading_week(WEEK_ID, _calendar({1, 4}))
    assert result.qualifies is False
    assert result.status is TradingWeekStatus.SHORT_WEEK
    assert result.trading_day_count == 2
    assert result.reason == "INSUFFICIENT_TRADING_DAYS"


def test_no_open_days_is_distinct_from_short_week() -> None:
    result = assess_trading_week(WEEK_ID, _calendar(set()))
    assert result.status is TradingWeekStatus.NO_OPEN_DAYS
    assert result.reason == "NO_TRADING_DAYS"


def test_missing_or_conflicted_calendar_blocks_qualification() -> None:
    missing_day = _calendar({0, 1, 2})[:-1]
    result = assess_trading_week(WEEK_ID, missing_day)
    assert result.status is TradingWeekStatus.DATA_DEGRADED
    assert result.qualifies is False

    conflicted = _calendar({0, 1, 2})
    conflicted[0] = TradingCalendarDay(
        conflicted[0].calendar_date,
        True,
        DataQuality.CONFLICTED,
    )
    assert assess_trading_week(WEEK_ID, conflicted).status is TradingWeekStatus.DATA_DEGRADED


def test_invalid_week_and_duplicate_dates_are_rejected() -> None:
    with pytest.raises(TradingCalendarError, match="must be a Monday"):
        assess_trading_week(WEEK_ID + timedelta(days=1), _calendar({0, 1, 2}))
    duplicate = _calendar({0, 1, 2})
    duplicate[-1] = duplicate[0]
    with pytest.raises(TradingCalendarError, match="duplicate"):
        assess_trading_week(WEEK_ID, duplicate)


def test_monday_holiday_uses_tuesday_preopen_and_open_as_entry() -> None:
    assessment = assess_trading_week(WEEK_ID, _calendar({1, 2, 3, 4}))
    schedule = build_trading_week_schedule(
        assessment,
        previous_open_date=date(2026, 7, 31),
    )
    assert schedule.decision_cutoff.isoformat() == "2026-07-31T15:00:00+08:00"
    assert schedule.publication_deadline.isoformat() == "2026-08-04T09:30:00+08:00"
    assert schedule.evaluation_entry_date == date(2026, 8, 4)
    assert schedule.review_date == date(2026, 8, 7)
    assert is_weekly_preopen_run_date(schedule, date(2026, 8, 4)) is True
    assert is_weekly_preopen_run_date(schedule, date(2026, 8, 3)) is False


def test_ineligible_week_cannot_be_scheduled() -> None:
    assessment = assess_trading_week(WEEK_ID, _calendar({0, 1}))
    with pytest.raises(TradingCalendarError, match="ineligible"):
        build_trading_week_schedule(
            assessment,
            previous_open_date=date(2026, 7, 31),
        )
