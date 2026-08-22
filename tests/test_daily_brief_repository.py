from datetime import date

import pytest
from pawe_api.briefs.repository import BriefGenerationError, _brief_inputs
from pawe_api.briefs.service import build_deterministic_brief_item
from pawe_api.contracts import DailyRiskStatus, DataQuality


def test_snapshot_payload_builds_first_open_daily_brief_inputs() -> None:
    target, market = _brief_inputs(
        "000001",
        "样本",
        _payload(),
        week_id=date(2026, 8, 10),
        trade_date=date(2026, 8, 11),
        quality=DataQuality.VERIFIED,
    )
    item = build_deterministic_brief_item(target, market)

    assert target.monday_open == 10.0
    assert market.previous_close == 10.5
    assert market.week_high == 11.5
    assert item.daily_return == pytest.approx(10.8 / 10.5 - 1)
    assert item.week_high_return == pytest.approx(0.15)
    assert item.risk_status is DailyRiskStatus.ON_TRACK


def test_snapshot_payload_requires_the_requested_trade_date() -> None:
    with pytest.raises(BriefGenerationError, match="current or previous"):
        _brief_inputs(
            "000001",
            "样本",
            _payload(),
            week_id=date(2026, 8, 10),
            trade_date=date(2026, 8, 12),
            quality=DataQuality.VERIFIED,
        )


def test_snapshot_prefers_longer_merged_backup_coverage() -> None:
    payload = _payload()
    payload["source_bars"] = {
        "tencent": [_payload()["source_bars"]["tencent"][0]],
        "eastmoney+sina": _payload()["source_bars"]["tencent"],
    }
    target, market = _brief_inputs(
        "000001",
        "样本",
        payload,
        week_id=date(2026, 8, 10),
        trade_date=date(2026, 8, 11),
        quality=DataQuality.DEGRADED,
    )

    assert target.monday_open == 10.0
    assert market.close == 10.8


def _payload() -> dict[str, object]:
    return {
        "source_bars": {
            "tencent": [
                {
                    "trade_date": "2026-08-07",
                    "open": "9.8",
                    "high": "10.1",
                    "close": "10.0",
                    "volume": "100",
                },
                {
                    "trade_date": "2026-08-10",
                    "open": "10.0",
                    "high": "11.0",
                    "close": "10.5",
                    "volume": "120",
                },
                {
                    "trade_date": "2026-08-11",
                    "open": "10.6",
                    "high": "11.5",
                    "close": "10.8",
                    "volume": "150",
                },
            ]
        }
    }
