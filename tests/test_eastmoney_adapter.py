import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from pawe_api.data.eastmoney import EastmoneyPayloadError, parse_qfq_daily_bars

FETCHED_AT = datetime(2026, 8, 3, 18, tzinfo=ZoneInfo("Asia/Shanghai"))


def _payload(rows: list[str], *, code: str = "300383", rc: int = 0) -> str:
    return json.dumps({"rc": rc, "data": {"code": code, "klines": rows}})


def test_parser_maps_qfq_ohlcv_and_amount() -> None:
    bars = parse_qfq_daily_bars(
        _payload(["2025-02-21,18.14,20.69,20.69,18.06,2335540,4562192759.00,15.26"]),
        stock_key="sz300383",
        expected_code="300383",
        start=date(2025, 2, 21),
        end=date(2025, 2, 28),
        fetched_at=FETCHED_AT,
    )
    assert str(bars[0].close) == "20.69"
    assert str(bars[0].amount) == "4562192759.00"
    assert bars[0].source == "eastmoney"


def test_parser_rejects_wrong_stock_and_error_code() -> None:
    with pytest.raises(EastmoneyPayloadError, match="unexpected stock"):
        parse_qfq_daily_bars(
            _payload(["2025-02-21,18,19,20,17,100,1000"], code="600519"),
            stock_key="sz300383",
            expected_code="300383",
            start=date(2025, 2, 21),
            end=date(2025, 2, 28),
            fetched_at=FETCHED_AT,
        )
    with pytest.raises(EastmoneyPayloadError, match="code is not zero"):
        parse_qfq_daily_bars(
            _payload([], rc=1),
            stock_key="sz300383",
            expected_code="300383",
            start=date(2025, 2, 21),
            end=date(2025, 2, 28),
            fetched_at=FETCHED_AT,
        )


def test_parser_rejects_out_of_window_and_empty_rows() -> None:
    with pytest.raises(EastmoneyPayloadError, match="outside"):
        parse_qfq_daily_bars(
            _payload(["2026-08-03,18,19,20,17,100,1000"]),
            stock_key="sz300383",
            expected_code="300383",
            start=date(2025, 2, 21),
            end=date(2025, 2, 28),
            fetched_at=FETCHED_AT,
        )
    with pytest.raises(EastmoneyPayloadError, match="empty"):
        parse_qfq_daily_bars(
            _payload([]),
            stock_key="sz300383",
            expected_code="300383",
            start=date(2025, 2, 21),
            end=date(2025, 2, 28),
            fetched_at=FETCHED_AT,
        )
