import pytest
from pawe_api.briefs.service import (
    DailyMarketSnapshot,
    PublishedTarget,
    build_deterministic_brief_item,
)
from pawe_api.contracts import DailyRiskStatus, DataQuality

TARGET = PublishedTarget(stock_code="002472", stock_name="双环传动", monday_open=100)


def test_brief_marks_target_touch_as_on_track() -> None:
    item = build_deterministic_brief_item(
        TARGET,
        DailyMarketSnapshot(
            previous_close=106,
            close=108,
            week_high=111,
            volume=120,
            previous_five_day_average_volume=100,
            quality=DataQuality.VERIFIED,
        ),
    )
    assert item.risk_status is DailyRiskStatus.ON_TRACK
    assert item.distance_to_target == 0
    assert item.week_high_return == pytest.approx(0.11)
    assert item.summary == "当日收盘较前收+1.89%；成交量为前5日均量的1.20倍。"
    assert "周内" not in item.summary


def test_brief_marks_large_pullback_as_risk() -> None:
    item = build_deterministic_brief_item(
        TARGET,
        DailyMarketSnapshot(
            previous_close=103,
            close=99,
            week_high=108,
            volume=80,
            previous_five_day_average_volume=100,
            quality=DataQuality.SINGLE_SOURCE,
        ),
    )
    assert item.risk_status is DailyRiskStatus.RISK_TRIGGERED


def test_brief_prioritizes_data_degradation() -> None:
    item = build_deterministic_brief_item(
        TARGET,
        DailyMarketSnapshot(
            previous_close=100,
            close=110,
            week_high=111,
            volume=120,
            previous_five_day_average_volume=100,
            quality=DataQuality.CONFLICTED,
        ),
    )
    assert item.risk_status is DailyRiskStatus.DATA_DEGRADED


def test_brief_rejects_impossible_week_high() -> None:
    with pytest.raises(ValueError, match="week_high"):
        build_deterministic_brief_item(
            TARGET,
            DailyMarketSnapshot(
                previous_close=100,
                close=101,
                week_high=100,
                volume=100,
                previous_five_day_average_volume=100,
                quality=DataQuality.VERIFIED,
            ),
        )
