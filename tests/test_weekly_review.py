from datetime import UTC, date, datetime

import pytest
from pawe_api.contracts import DataQuality
from pawe_api.evaluation.repository import ReviewTarget, compute_weekly_review
from pawe_api.evaluation.weekly import WeeklyBar


def _bars() -> tuple[WeeklyBar, ...]:
    return (
        WeeklyBar(date(2026, 8, 3), 10.0, 10.5, 9.8, 10.2),
        WeeklyBar(date(2026, 8, 4), 10.2, 11.1, 10.0, 10.9),
        WeeklyBar(date(2026, 8, 5), 10.9, 11.2, 10.5, 10.7),
        WeeklyBar(date(2026, 8, 6), 10.7, 10.9, 10.1, 10.4),
        WeeklyBar(date(2026, 8, 7), 10.4, 10.8, 10.2, 10.6),
    )


def test_weekly_review_calculates_target_benchmark_and_industry_excess() -> None:
    computed = compute_weekly_review(
        week_id=date(2026, 8, 3),
        source_type="historical_replay",
        source_version=1,
        rule_version="v9.0.0",
        targets=(
            ReviewTarget(1, "600001", "测试", 1, _bars(), industry_return=0.03),
        ),
        as_of=datetime(2026, 8, 7, 17, 30, tzinfo=UTC),
        generated_at=datetime(2026, 8, 10, 9, tzinfo=UTC),
        quality=DataQuality.DEGRADED,
        benchmark_return=0.02,
        warnings=("RETROSPECTIVE_FETCH_AFTER_SIMULATED_TIME",),
    )

    assert computed.entry_trade_date == date(2026, 8, 3)
    assert computed.final_trade_date == date(2026, 8, 7)
    assert computed.aggregate["target_touched_count"] == 1
    assert computed.aggregate["average_benchmark_excess"] == pytest.approx(0.04)
    assert computed.aggregate["average_industry_excess"] == pytest.approx(0.03)
    assert "研究性回放" in computed.summary


def test_weekly_review_rejects_targets_with_different_time_windows() -> None:
    shifted = _bars()[1:]
    with pytest.raises(ValueError, match="complete trading week"):
        compute_weekly_review(
            week_id=date(2026, 8, 3),
            source_type="historical_replay",
            source_version=1,
            rule_version="v9.0.0",
            targets=(
                ReviewTarget(1, "600001", "甲", 1, _bars()),
                ReviewTarget(2, "600002", "乙", 2, shifted),
            ),
            as_of=datetime(2026, 8, 7, 17, 30, tzinfo=UTC),
            generated_at=datetime(2026, 8, 10, 9, tzinfo=UTC),
            quality=DataQuality.DEGRADED,
            benchmark_return=None,
        )
