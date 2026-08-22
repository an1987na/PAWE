from datetime import date

import pytest
from pawe_api.evaluation.weekly import WeeklyBar, WeeklyEvaluationError, evaluate_weekly_path


def _bars() -> list[WeeklyBar]:
    return [
        WeeklyBar(date(2026, 7, 27), 10.0, 10.5, 9.5, 10.2),
        WeeklyBar(date(2026, 7, 28), 10.2, 11.1, 10.0, 10.8),
        WeeklyBar(date(2026, 7, 29), 10.8, 11.3, 10.6, 11.0),
        WeeklyBar(date(2026, 7, 30), 11.0, 11.2, 10.4, 10.5),
        WeeklyBar(date(2026, 7, 31), 10.5, 10.8, 10.0, 10.3),
    ]


def test_weekly_metrics_use_first_session_open_and_fixed_ten_percent_target() -> None:
    result = evaluate_weekly_path(_bars())
    assert result.entry_price == 10.0
    assert result.week_high_return == pytest.approx(0.13)
    assert result.week_close_return == pytest.approx(0.03)
    assert result.max_drawdown_from_entry == pytest.approx(-0.05)
    assert result.max_peak_to_trough_drawdown == pytest.approx(10.0 / 11.3 - 1)
    assert result.target_touched is True
    assert result.target_touch_date == date(2026, 7, 28)
    assert result.drawdown_before_touch == pytest.approx(-0.05)
    assert result.touch_intraday_order_unknown is True


def test_near_target_is_eight_to_ten_percent_only() -> None:
    bars = [WeeklyBar(date(2026, 7, 27), 10.0, 10.9, 9.8, 10.5)]
    result = evaluate_weekly_path(bars)
    assert result.target_touched is False
    assert result.near_target is True
    assert result.drawdown_before_touch is None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"suspended_at_open": True}, "suspended_at_entry"),
        ({"limit_up_at_open": True}, "limit_up_at_entry"),
    ],
)
def test_entry_accessibility_is_reported_without_rewriting_market_path(
    changes: dict[str, bool], reason: str
) -> None:
    first = WeeklyBar(date(2026, 7, 27), 10.0, 10.9, 9.8, 10.5, **changes)
    result = evaluate_weekly_path([first])
    assert result.accessible_at_entry is False
    assert result.accessibility_reason == reason
    assert result.week_high_return == pytest.approx(0.09)


def test_invalid_or_unsorted_bars_are_rejected() -> None:
    with pytest.raises(WeeklyEvaluationError, match="strictly ordered"):
        evaluate_weekly_path(list(reversed(_bars())))
    with pytest.raises(WeeklyEvaluationError, match="invalid OHLC"):
        evaluate_weekly_path([WeeklyBar(date(2026, 7, 27), 10.0, 9.0, 9.5, 10.2)])
