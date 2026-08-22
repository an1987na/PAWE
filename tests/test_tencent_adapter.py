import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from pawe_api.data.tencent import TencentPayloadError, parse_qfq_daily_bars

FETCHED_AT = datetime(2026, 8, 3, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def _payload(rows: list[list[str]]) -> str:
    return json.dumps(
        {
            "code": 0,
            "data": {
                "sz300383": {
                    "qfqday": rows,
                    "qt": {"sz300383": ["current", "2026-08-03", "9999.00"]},
                }
            },
        }
    )


def test_parser_uses_only_requested_historical_qfq_array() -> None:
    bars = parse_qfq_daily_bars(
        _payload([["2025-02-21", "18.140", "20.690", "20.690", "18.060", "2335540"]]),
        stock_key="sz300383",
        start=date(2025, 2, 21),
        end=date(2025, 2, 28),
        fetched_at=FETCHED_AT,
    )
    assert len(bars) == 1
    assert bars[0].trade_date == date(2025, 2, 21)
    assert str(bars[0].close) == "20.690"
    assert all(bar.trade_date.year == 2025 for bar in bars)


def test_parser_rejects_dates_outside_requested_window() -> None:
    with pytest.raises(TencentPayloadError, match="outside"):
        parse_qfq_daily_bars(
            _payload([["2026-08-03", "18", "19", "20", "17", "100"]]),
            stock_key="sz300383",
            start=date(2025, 2, 21),
            end=date(2025, 2, 28),
            fetched_at=FETCHED_AT,
        )


def test_parser_rejects_inconsistent_ohlc() -> None:
    with pytest.raises(TencentPayloadError, match="high price"):
        parse_qfq_daily_bars(
            _payload([["2025-02-21", "18", "21", "20", "17", "100"]]),
            stock_key="sz300383",
            start=date(2025, 2, 21),
            end=date(2025, 2, 28),
            fetched_at=FETCHED_AT,
        )
