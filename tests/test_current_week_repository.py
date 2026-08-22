from datetime import date

from pawe_api.decisions.repository import decision_display_week_bounds, natural_week_bounds


def test_natural_week_bounds_keep_holiday_shifted_entry_in_same_week() -> None:
    assert natural_week_bounds(date(2026, 8, 4)) == (
        date(2026, 8, 3),
        date(2026, 8, 9),
    )


def test_decision_display_bounds_keep_only_the_current_trading_week() -> None:
    assert decision_display_week_bounds(date(2026, 8, 9)) == (
        date(2026, 8, 3),
        date(2026, 8, 9),
    )
