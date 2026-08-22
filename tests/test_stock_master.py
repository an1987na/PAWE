import json
from datetime import UTC, date, datetime

import httpx
import pytest
from pawe_api.contracts import DataQuality
from pawe_api.data.providers import (
    EastmoneyStockMasterProvider,
    ProviderPolicy,
)
from pawe_api.data.stock_master import (
    Exchange,
    StockMasterPayloadError,
    parse_eastmoney_stock_master_page,
)

FETCHED_AT = datetime(2026, 8, 9, tzinfo=UTC)
POLICY = ProviderPolicy(timeout_seconds=1, retry_count=0, min_interval_seconds=0)


def _payload(rows: list[dict[str, object]], *, total: int | None = None) -> str:
    return json.dumps(
        {"rc": 0, "data": {"total": total or len(rows), "diff": rows}},
        ensure_ascii=False,
    )


@pytest.mark.parametrize(
    ("code", "market", "name", "exchange", "board", "status"),
    [
        ("600519", 1, "贵州茅台", Exchange.SSE, "main", "active"),
        ("688981", 1, "中芯国际", Exchange.SSE, "star", "active"),
        ("000001", 0, "平安银行", Exchange.SZSE, "main", "active"),
        ("300750", 0, "宁德时代", Exchange.SZSE, "gem", "active"),
        ("302132", 0, "中航成飞", Exchange.SZSE, "gem", "active"),
        ("920001", 0, "北交样本", Exchange.BSE, "bse", "active"),
        ("830001", 0, "旧码样本", Exchange.BSE, "bse", "active"),
        ("002001", 0, "*ST样本", Exchange.SZSE, "main", "st"),
        ("600001", 1, "退市样本", Exchange.SSE, "main", "delisting"),
    ],
)
def test_stock_master_normalizes_exchange_board_and_status(
    code: str,
    market: int,
    name: str,
    exchange: Exchange,
    board: str,
    status: str,
) -> None:
    page = parse_eastmoney_stock_master_page(
        _payload(
            [
                {
                    "f12": code,
                    "f13": market,
                    "f14": name,
                    "f26": 20200102,
                    "f100": "半导体",
                }
            ]
        ),
        fetched_at=FETCHED_AT,
    )
    record = page.records[0]
    assert (record.exchange, record.board, record.status) == (exchange, board, status)
    assert record.listing_date == date(2020, 1, 2)
    assert record.provider_industry == "半导体"
    assert record.quality is DataQuality.SINGLE_SOURCE
    assert len(record.content_hash) == 64


def test_stock_master_rejects_bad_rows_without_guessing() -> None:
    page = parse_eastmoney_stock_master_page(
        _payload(
            [
                {"f12": "600519", "f13": 1, "f14": "贵州茅台", "f26": 20010827},
                {"f12": "123", "f13": 0, "f14": "坏代码", "f26": 20200101},
            ]
        ),
        fetched_at=FETCHED_AT,
    )
    assert len(page.records) == 1
    assert page.warnings == ("row_1_rejected:code or name is invalid",)


def test_stock_master_rejects_invalid_payload() -> None:
    with pytest.raises(StockMasterPayloadError, match="valid JSON"):
        parse_eastmoney_stock_master_page("not-json", fetched_at=FETCHED_AT)


@pytest.mark.asyncio
async def test_stock_master_provider_paginates_and_checks_coverage() -> None:
    rows = [
        {"f12": "600519", "f13": 1, "f14": "贵州茅台", "f26": 20010827},
        {"f12": "000001", "f13": 0, "f14": "平安银行", "f26": 19910403},
        {"f12": "920001", "f13": 0, "f14": "北交样本", "f26": 20250102},
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["pn"])
        assert request.url.params["fields"] == "f12,f13,f14,f26,f100"
        start = (page - 1) * 2
        return httpx.Response(200, text=_payload(rows[start : start + 2], total=3))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        batch = await EastmoneyStockMasterProvider(client, policy=POLICY).fetch(
            page_size=2
        )
    assert batch.expected_total == 3
    assert [record.code for record in batch.records] == ["600519", "000001", "920001"]
    assert batch.warnings == ()
