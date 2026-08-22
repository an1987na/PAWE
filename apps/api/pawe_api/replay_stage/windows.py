from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from pawe_api.data.calendar import SHANGHAI


class ReplayStage(StrEnum):
    WEEKLY_SELECTION = "weekly_selection"
    DAILY_BRIEF = "daily_brief"
    WEEKLY_REVIEW = "weekly_review"


class ReplayWindowError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReplayWindow:
    stage: ReplayStage
    mode: str
    eligible: bool
    simulated_cutoff: datetime
    reason: str


def classify_replay_window(
    stage: ReplayStage,
    *,
    now: datetime,
    week_id: date,
    trade_date: date | None = None,
    first_open_date: date | None = None,
    previous_open_date: date | None = None,
    final_open_date: date | None = None,
    next_trading_week_start: date | None = None,
) -> ReplayWindow:
    """Classify formal/replay windows without changing each stage's data cutoff.

    A target week remains formal until the first open day of the next trading
    week.  Late execution inside that window is a formal catch-up, while its
    information cutoff remains the original pre-week/day/week close.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ReplayWindowError("now must be timezone-aware")
    local_now = now.astimezone(SHANGHAI)
    formal_end = datetime.combine(
        next_trading_week_start or (week_id + timedelta(days=7)),
        time(0),
        tzinfo=SHANGHAI,
    )
    if stage is ReplayStage.WEEKLY_SELECTION:
        first_open = first_open_date or week_id
        cutoff = datetime.combine(
            previous_open_date or (first_open - timedelta(days=1)),
            time(15),
            tzinfo=SHANGHAI,
        )
        if local_now < cutoff:
            raise ReplayWindowError("weekly selection information cutoff is not complete")
        return ReplayWindow(
            stage,
            "formal" if local_now < formal_end else "replay",
            True,
            cutoff,
            "current_trading_week_formal"
            if local_now < formal_end
            else "after_next_trading_week_started",
        )
    if stage is ReplayStage.DAILY_BRIEF:
        if trade_date is None:
            raise ReplayWindowError("trade_date is required for a daily replay")
        formal_start = datetime.combine(trade_date, time(15, 30), tzinfo=SHANGHAI)
        cutoff = datetime.combine(trade_date, time(15), tzinfo=SHANGHAI)
        if local_now < formal_start:
            raise ReplayWindowError("daily brief is not due until 15:30")
        return ReplayWindow(
            stage,
            "formal" if local_now < formal_end else "replay",
            True,
            cutoff,
            "current_trading_week_formal"
            if local_now < formal_end
            else "after_next_trading_week_started",
        )
    if final_open_date is None:
        raise ReplayWindowError("final_open_date is required for a weekly review")
    formal_start = datetime.combine(final_open_date, time(15, 30), tzinfo=SHANGHAI)
    cutoff = formal_start
    if local_now < formal_start:
        raise ReplayWindowError("weekly review is not due until the final close")
    return ReplayWindow(
        stage,
        "formal" if local_now < formal_end else "replay",
        True,
        cutoff,
        "current_trading_week_formal"
        if local_now < formal_end
        else "after_next_trading_week_started",
    )


def replay_stage_order(stage: ReplayStage) -> tuple[ReplayStage, ...]:
    if stage is ReplayStage.WEEKLY_SELECTION:
        return (ReplayStage.WEEKLY_SELECTION,)
    if stage is ReplayStage.DAILY_BRIEF:
        return (ReplayStage.WEEKLY_SELECTION, ReplayStage.DAILY_BRIEF)
    return (
        ReplayStage.WEEKLY_SELECTION,
        ReplayStage.DAILY_BRIEF,
        ReplayStage.WEEKLY_REVIEW,
    )
