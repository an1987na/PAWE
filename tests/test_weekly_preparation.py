from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pawe_api.contracts import DataQuality, MarketState
from pawe_api.data.calendar import (
    TradingCalendarDay,
    assess_trading_week,
    build_trading_week_schedule,
)
from pawe_api.data.snapshot import SnapshotRecord, freeze_snapshot
from pawe_api.rules.market_state import MarketStateInput, PoolMetrics
from pawe_api.weekly import WeeklyRunGate, prepare_weekly_rule_baseline
from rule_factory import rule_features

SHANGHAI = ZoneInfo("Asia/Shanghai")
WEEK_ID = date(2026, 8, 3)


def _assessment(open_offsets: set[int] | None = None):
    effective_offsets = {1, 2, 3, 4} if open_offsets is None else open_offsets
    days = [
        TradingCalendarDay(
            WEEK_ID + timedelta(days=offset),
            offset in effective_offsets,
            DataQuality.VERIFIED,
        )
        for offset in range(5)
    ]
    return assess_trading_week(WEEK_ID, days)


def _schedule():
    return build_trading_week_schedule(
        _assessment(),
        previous_open_date=date(2026, 7, 31),
    )


def _snapshot(cutoff: datetime | None = None):
    expected_cutoff = cutoff or datetime(2026, 7, 31, 15, tzinfo=SHANGHAI)
    locked_at = datetime(2026, 8, 3, 20, tzinfo=SHANGHAI)
    return freeze_snapshot(
        [
            SnapshotRecord(
                source="fixture",
                as_of=expected_cutoff,
                fetched_at=locked_at,
                quality=DataQuality.VERIFIED,
                payload={"week": "monday_holiday"},
            )
        ],
        cutoff=expected_cutoff,
        locked_at=locked_at,
    )


def _state_input(coverage: float = 1.0) -> MarketStateInput:
    pool = PoolMetrics(0.08, 0.20, 0.60, 0.02, coverage)
    return MarketStateInput(
        previous_state=MarketState.NORMAL,
        shanghai_close_return=0.01,
        gem_close_return=0.02,
        star50_close_return=0.03,
        main_pool=pool,
        reserve_pool=pool,
        main_average_without_strongest=0.07,
        strong_reserve_positive_close_ratio=0.60,
        qualifying_recovery_sector_count=2,
    )


def _features():
    return [
        rule_features(
            stock_code=f"00000{index}",
            stock_name=f"样本{index}",
            primary_sector=f"sector{index}",
        )
        for index in range(1, 6)
    ]


def test_monday_holiday_prepares_rule_baseline_on_tuesday_before_open() -> None:
    result = prepare_weekly_rule_baseline(
        now=datetime(2026, 8, 4, 8, 30, tzinfo=SHANGHAI),
        assessment=_assessment(),
        schedule=_schedule(),
        snapshot=_snapshot(),
        features=_features(),
        market_state_input=_state_input(),
    )
    assert result.gate is WeeklyRunGate.READY_FOR_APPROVAL
    assert result.rule_result is not None
    assert len(result.rule_result.baseline.items) == 5


def test_post_cutoff_and_in_week_catchup_are_allowed() -> None:
    monday = prepare_weekly_rule_baseline(
        now=datetime(2026, 8, 3, 8, 30, tzinfo=SHANGHAI),
        assessment=_assessment(),
        schedule=_schedule(),
        snapshot=_snapshot(),
        features=_features(),
        market_state_input=_state_input(),
    )
    assert monday.gate is WeeklyRunGate.READY_FOR_APPROVAL

    late = prepare_weekly_rule_baseline(
        now=datetime(2026, 8, 4, 9, 31, tzinfo=SHANGHAI),
        assessment=_assessment(),
        schedule=_schedule(),
        snapshot=_snapshot(),
        features=_features(),
        market_state_input=_state_input(),
    )
    assert late.gate is WeeklyRunGate.READY_FOR_APPROVAL

    next_week = prepare_weekly_rule_baseline(
        now=datetime(2026, 8, 10, 0, 0, tzinfo=SHANGHAI),
        assessment=_assessment(),
        schedule=_schedule(),
        snapshot=_snapshot(),
        features=_features(),
        market_state_input=_state_input(),
    )
    assert next_week.gate is WeeklyRunGate.PUBLICATION_DEADLINE_PASSED


def test_preparation_before_decision_cutoff_is_blocked() -> None:
    result = prepare_weekly_rule_baseline(
        now=datetime(2026, 7, 31, 14, 59, tzinfo=SHANGHAI),
        assessment=_assessment(),
        schedule=_schedule(),
        snapshot=_snapshot(),
        features=_features(),
        market_state_input=_state_input(),
    )

    assert result.gate is WeeklyRunGate.PREPARATION_WINDOW_NOT_OPEN


def test_snapshot_cutoff_mismatch_and_rule_degradation_block_run() -> None:
    mismatch = prepare_weekly_rule_baseline(
        now=datetime(2026, 8, 4, 8, 30, tzinfo=SHANGHAI),
        assessment=_assessment(),
        schedule=_schedule(),
        snapshot=_snapshot(datetime(2026, 7, 30, 15, tzinfo=SHANGHAI)),
        features=_features(),
        market_state_input=_state_input(),
    )
    assert mismatch.gate is WeeklyRunGate.SNAPSHOT_CUTOFF_MISMATCH

    degraded = prepare_weekly_rule_baseline(
        now=datetime(2026, 8, 4, 8, 30, tzinfo=SHANGHAI),
        assessment=_assessment(),
        schedule=_schedule(),
        snapshot=_snapshot(),
        features=_features(),
        market_state_input=_state_input(0.79),
    )
    assert degraded.gate is WeeklyRunGate.RULE_BLOCKED
    assert degraded.rule_result is not None
    assert "STATE_DATA_DEGRADED" in degraded.rule_result.flags


def test_backup_only_calendar_stops_weekly_preparation() -> None:
    days = [
        TradingCalendarDay(
            WEEK_ID + timedelta(days=offset),
            offset in {1, 2, 3, 4},
            DataQuality.DEGRADED,
        )
        for offset in range(5)
    ]
    assessment = assess_trading_week(WEEK_ID, days)
    schedule = build_trading_week_schedule(
        assessment,
        previous_open_date=date(2026, 7, 31),
    )
    result = prepare_weekly_rule_baseline(
        now=datetime(2026, 8, 4, 8, 30, tzinfo=SHANGHAI),
        assessment=assessment,
        schedule=schedule,
        snapshot=_snapshot(),
        features=_features(),
        market_state_input=_state_input(),
    )
    assert result.gate is WeeklyRunGate.CALENDAR_DATA_DEGRADED
    assert result.rule_result is None
