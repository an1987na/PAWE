from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from pawe_api.contracts import DataQuality

SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_MARKET_OPEN = time(9, 30)


class TradingCalendarError(ValueError):
    pass


class TradingWeekStatus(StrEnum):
    ELIGIBLE = "eligible"
    SHORT_WEEK = "short_week"
    NO_OPEN_DAYS = "no_open_days"
    DATA_DEGRADED = "data_degraded"


@dataclass(frozen=True, slots=True)
class TradingCalendarDay:
    calendar_date: date
    is_open: bool
    quality: DataQuality


@dataclass(frozen=True, slots=True)
class TradingCalendarObservation:
    source: str
    week_id: date
    open_dates: tuple[date, ...]
    quality: DataQuality


@dataclass(frozen=True, slots=True)
class TradingWeekAssessment:
    week_id: date
    status: TradingWeekStatus
    qualifies: bool
    data_quality: DataQuality
    open_dates: tuple[date, ...]
    trading_day_count: int
    longest_consecutive_run: int
    first_open_date: date | None
    last_open_date: date | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class TradingWeekSchedule:
    week_id: date
    decision_cutoff: datetime
    publication_deadline: datetime
    evaluation_entry_date: date
    review_date: date


def assess_trading_week(
    week_id: date,
    calendar_days: list[TradingCalendarDay],
    *,
    minimum_trading_days: int = 3,
) -> TradingWeekAssessment:
    if week_id.weekday() != 0:
        raise TradingCalendarError("week_id must be a Monday")
    if not 1 <= minimum_trading_days <= 5:
        raise TradingCalendarError("minimum trading days must be between one and five")
    expected_dates = tuple(week_id + timedelta(days=offset) for offset in range(5))
    dates = [day.calendar_date for day in calendar_days]
    if len(dates) != len(set(dates)):
        raise TradingCalendarError("trading calendar contains duplicate dates")
    if any(day not in expected_dates for day in dates):
        raise TradingCalendarError("trading calendar contains dates outside the requested week")

    by_date = {day.calendar_date: day for day in calendar_days}
    if any(day not in by_date for day in expected_dates) or any(
        day.quality in {DataQuality.CONFLICTED, DataQuality.MISSING} for day in calendar_days
    ):
        return TradingWeekAssessment(
            week_id=week_id,
            status=TradingWeekStatus.DATA_DEGRADED,
            qualifies=False,
            data_quality=(
                DataQuality.CONFLICTED
                if any(day.quality is DataQuality.CONFLICTED for day in calendar_days)
                else DataQuality.MISSING
            ),
            open_dates=tuple(sorted(day.calendar_date for day in calendar_days if day.is_open)),
            trading_day_count=sum(day.is_open for day in calendar_days),
            longest_consecutive_run=0,
            first_open_date=None,
            last_open_date=None,
            reason="TRADING_CALENDAR_DEGRADED",
        )

    open_dates = tuple(day for day in expected_dates if by_date[day].is_open)
    longest_run = _longest_calendar_day_run(open_dates)
    if not open_dates:
        return TradingWeekAssessment(
            week_id=week_id,
            status=TradingWeekStatus.NO_OPEN_DAYS,
            qualifies=False,
            data_quality=_calendar_quality(calendar_days),
            open_dates=(),
            trading_day_count=0,
            longest_consecutive_run=0,
            first_open_date=None,
            last_open_date=None,
            reason="NO_TRADING_DAYS",
        )
    qualifies = len(open_dates) >= minimum_trading_days
    return TradingWeekAssessment(
        week_id=week_id,
        status=TradingWeekStatus.ELIGIBLE if qualifies else TradingWeekStatus.SHORT_WEEK,
        qualifies=qualifies,
        data_quality=_calendar_quality(calendar_days),
        open_dates=open_dates,
        trading_day_count=len(open_dates),
        longest_consecutive_run=longest_run,
        first_open_date=open_dates[0],
        last_open_date=open_dates[-1],
        reason=None if qualifies else "INSUFFICIENT_TRADING_DAYS",
    )


def reconcile_trading_calendar(
    week_id: date,
    primary: TradingCalendarObservation | None,
    backup: TradingCalendarObservation | None,
) -> list[TradingCalendarDay]:
    expected_dates = tuple(week_id + timedelta(days=offset) for offset in range(5))
    if week_id.weekday() != 0:
        raise TradingCalendarError("week_id must be a Monday")
    if primary is None and backup is None:
        return [TradingCalendarDay(day, False, DataQuality.MISSING) for day in expected_dates]
    if primary is None:
        assert backup is not None
        _validate_observation(backup, week_id, expected_dates)
        return _calendar_days(expected_dates, backup.open_dates, DataQuality.DEGRADED)
    _validate_observation(primary, week_id, expected_dates)
    if backup is None:
        return _calendar_days(expected_dates, primary.open_dates, DataQuality.SINGLE_SOURCE)
    _validate_observation(backup, week_id, expected_dates)
    if primary.open_dates != backup.open_dates:
        return _calendar_days(expected_dates, (), DataQuality.CONFLICTED)
    quality = (
        DataQuality.DEGRADED
        if DataQuality.DEGRADED in {primary.quality, backup.quality}
        else DataQuality.VERIFIED
    )
    return _calendar_days(expected_dates, primary.open_dates, quality)


def build_trading_week_schedule(
    assessment: TradingWeekAssessment,
    *,
    previous_open_date: date,
    market_open_time: time = DEFAULT_MARKET_OPEN,
    timezone: ZoneInfo = SHANGHAI,
) -> TradingWeekSchedule:
    if not assessment.qualifies:
        raise TradingCalendarError("cannot schedule an ineligible trading week")
    first_open_date = assessment.first_open_date
    last_open_date = assessment.last_open_date
    assert first_open_date is not None and last_open_date is not None
    if previous_open_date >= first_open_date:
        raise TradingCalendarError("previous_open_date must precede the first weekly session")
    return TradingWeekSchedule(
        week_id=assessment.week_id,
        decision_cutoff=datetime.combine(previous_open_date, time(15, 0), tzinfo=timezone),
        publication_deadline=datetime.combine(first_open_date, market_open_time, tzinfo=timezone),
        evaluation_entry_date=first_open_date,
        review_date=last_open_date,
    )


def is_weekly_preopen_run_date(schedule: TradingWeekSchedule, run_date: date) -> bool:
    return run_date == schedule.evaluation_entry_date


def _longest_calendar_day_run(open_dates: tuple[date, ...]) -> int:
    longest = 0
    current = 0
    previous: date | None = None
    for trading_day in open_dates:
        current = (
            current + 1
            if previous is not None and trading_day - previous == timedelta(days=1)
            else 1
        )
        longest = max(longest, current)
        previous = trading_day
    return longest


def _validate_observation(
    observation: TradingCalendarObservation,
    week_id: date,
    expected_dates: tuple[date, ...],
) -> None:
    if observation.week_id != week_id:
        raise TradingCalendarError("calendar observation refers to another week")
    if len(observation.open_dates) != len(set(observation.open_dates)):
        raise TradingCalendarError("calendar observation contains duplicate open dates")
    if any(day not in expected_dates for day in observation.open_dates):
        raise TradingCalendarError("calendar observation contains an out-of-week date")
    if observation.quality in {DataQuality.CONFLICTED, DataQuality.MISSING}:
        raise TradingCalendarError("calendar observation is not usable")


def _calendar_days(
    expected_dates: tuple[date, ...],
    open_dates: tuple[date, ...],
    quality: DataQuality,
) -> list[TradingCalendarDay]:
    open_set = set(open_dates)
    return [TradingCalendarDay(day, day in open_set, quality) for day in expected_dates]


def _calendar_quality(calendar_days: list[TradingCalendarDay]) -> DataQuality:
    qualities = {day.quality for day in calendar_days}
    if DataQuality.DEGRADED in qualities:
        return DataQuality.DEGRADED
    if qualities == {DataQuality.VERIFIED}:
        return DataQuality.VERIFIED
    return DataQuality.SINGLE_SOURCE
