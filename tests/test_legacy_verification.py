from datetime import date

import pytest
from pawe_api.evaluation.weekly import WeeklyBar, evaluate_weekly_path
from pawe_api.experiments.legacy import LegacyBucket, LegacyItem
from pawe_api.experiments.verification import verify_legacy_item_metrics


def _bars() -> list[WeeklyBar]:
    return [
        WeeklyBar(date(2025, 2, 17), 17.45, 18.30, 17.07, 17.77),
        WeeklyBar(date(2025, 2, 18), 17.19, 17.19, 15.79, 16.21),
        WeeklyBar(date(2025, 2, 19), 16.15, 16.90, 16.10, 16.77),
        WeeklyBar(date(2025, 2, 20), 16.52, 17.58, 16.43, 17.23),
        WeeklyBar(date(2025, 2, 21), 18.14, 20.69, 18.06, 20.69),
    ]


def test_legacy_claims_are_recalculated_with_declared_baseline() -> None:
    item = LegacyItem(
        bucket=LegacyBucket.MAIN,
        stock_code="300383",
        stock_name="光环新网",
        baseline_price=17.62,
        week_high_return=0.1742,
        close_return=0.1742,
        max_drawdown=-0.1039,
    )
    result = verify_legacy_item_metrics(item, _bars())
    assert result.status == "verified"
    assert len(result.metrics) == 3
    assert all(metric.within_tolerance for metric in result.metrics)


def test_v9_standardized_replay_keeps_first_session_open_separate() -> None:
    result = evaluate_weekly_path(_bars())
    assert result.entry_price == 17.45
    assert result.week_high_return == pytest.approx(20.69 / 17.45 - 1)
    assert result.week_high_return != pytest.approx(0.1742)


def test_conflict_and_insufficient_data_are_not_silently_verified() -> None:
    conflicted = LegacyItem(
        bucket=LegacyBucket.MAIN,
        stock_code="300383",
        stock_name="光环新网",
        baseline_price=17.62,
        week_high_return=0.10,
    )
    assert verify_legacy_item_metrics(conflicted, _bars()).status == "conflicted"

    missing = LegacyItem(
        bucket=LegacyBucket.MAIN,
        stock_code="300383",
        stock_name="光环新网",
    )
    assert verify_legacy_item_metrics(missing, _bars()).status == "insufficient_data"


def test_negative_verification_tolerance_is_rejected() -> None:
    item = LegacyItem(LegacyBucket.MAIN, "300383", "光环新网", baseline_price=17.62)
    with pytest.raises(ValueError, match="cannot be negative"):
        verify_legacy_item_metrics(item, _bars(), tolerance=-0.1)
