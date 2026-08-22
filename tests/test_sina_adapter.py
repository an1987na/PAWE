from datetime import UTC, date, datetime

import pytest
from pawe_api.data.sina import SinaPayloadError, parse_qfq_daily_rows


def test_parses_sina_qfq_rows_with_amount() -> None:
    bars = parse_qfq_daily_rows(
        [
            {
                "date": date(2026, 8, 7),
                "open": 10.1,
                "high": 10.5,
                "low": 10.0,
                "close": 10.4,
                "volume": 100_000,
                "amount": 103_000_000,
            }
        ],
        stock_key="sz000027",
        start=date(2026, 8, 1),
        end=date(2026, 8, 7),
        fetched_at=datetime(2026, 8, 9, tzinfo=UTC),
    )

    assert bars[0].source == "sina"
    assert bars[0].amount == 103_000_000
    assert bars[0].volume == 1000
    assert bars[0].adjustment == "qfq"


def test_rejects_sina_rows_outside_the_requested_window() -> None:
    with pytest.raises(SinaPayloadError, match="outside"):
        parse_qfq_daily_rows(
            [
                {
                    "date": "2026-07-31",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10,
                    "volume": 100,
                    "amount": 1000,
                }
            ],
            stock_key="sz000027",
            start=date(2026, 8, 1),
            end=date(2026, 8, 7),
            fetched_at=datetime(2026, 8, 9, tzinfo=UTC),
        )
