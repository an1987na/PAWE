import json
from datetime import UTC, date, datetime

import httpx
import pytest
from pawe_api.data.official_stock_master import (
    parse_bse_stock_master_page,
    parse_sse_kshare_list,
    parse_sse_stock_master_page,
    parse_szse_stock_master_page,
)
from pawe_api.data.providers import (
    BseStockMasterProvider,
    OfficialStockMasterProvider,
    ProviderPolicy,
    SseStockMasterProvider,
    StockMasterProviderError,
    SzseStockMasterProvider,
)
from pawe_api.data.stock_master import Exchange

FETCHED_AT = datetime(2026, 8, 9, tzinfo=UTC)
POLICY = ProviderPolicy(timeout_seconds=1, retry_count=0, min_interval_seconds=0)


def _sse_payload(code: str, *, total: int = 1, page_count: int = 1) -> str:
    return json.dumps(
        {
            "pageHelp": {"total": total, "pageCount": page_count},
            "result": [
                {
                    "A_STOCK_CODE": code,
                    "SEC_NAME_CN": "沪市样本",
                    "LIST_DATE": "20200102",
                    "CSRC_CODE_DESC": "制造业",
                }
            ],
        },
        ensure_ascii=False,
    )


def _szse_payload() -> str:
    return json.dumps(
        [
            {
                "metadata": {
                    "tabkey": "tab1",
                    "recordcount": 1,
                    "pagecount": 1,
                },
                "data": [
                    {
                        "agdm": "300750",
                        "agjc": "<a><u>宁德时代</u></a>",
                        "agssrq": "2018-06-11",
                        "sshymc": "C 制造业",
                    }
                ],
            },
            {
                "metadata": {"tabkey": "tab2"},
                "data": [],
            },
        ],
        ensure_ascii=False,
    )


def _bse_payload() -> str:
    return json.dumps(
        {
            "content": [
                {
                    "total": 1,
                    "totalPages": 1,
                    "data": [
                        {
                            "xxzqdm": "920001",
                            "xxzqjc": "北证样本",
                            "ssrq": "2025-01-02",
                            "sshymc": "C 制造业",
                        }
                    ],
                }
            ]
        },
        ensure_ascii=False,
    )


def _kshare_payload() -> str:
    return json.dumps(
        {
            "total": 1,
            "list": [["688981", "中芯国际"]],
        },
        ensure_ascii=False,
    )


def test_parses_sse_official_page() -> None:
    page = parse_sse_stock_master_page(_sse_payload("600519"), fetched_at=FETCHED_AT)
    assert page.exchange is Exchange.SSE
    assert page.total == 1
    assert page.records[0].code == "600519"
    assert page.records[0].source == "sse"


def test_parses_sse_kshare_coverage_list() -> None:
    result = parse_sse_kshare_list(_kshare_payload())
    assert result.total == 1
    assert result.securities == (("688981", "中芯国际"),)


def test_parses_szse_official_page_and_strips_link_markup() -> None:
    page = parse_szse_stock_master_page(_szse_payload(), fetched_at=FETCHED_AT)
    record = page.records[0]
    assert record.exchange is Exchange.SZSE
    assert record.board == "gem"
    assert record.name == "宁德时代"
    assert record.provider_industry == "C 制造业"
    assert record.listing_date == date(2018, 6, 11)


def test_parses_bse_official_page() -> None:
    page = parse_bse_stock_master_page(_bse_payload(), fetched_at=FETCHED_AT)
    assert page.exchange is Exchange.BSE
    assert page.records[0].code == "920001"
    assert page.records[0].board == "bse"


@pytest.mark.asyncio
async def test_official_provider_requires_complete_three_exchange_batch() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "query.sse.com.cn":
            assert request.url.params["pageHelp.cacheSize"] == "1"
            assert request.url.params["sqlId"] == "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L"
            code = "600519" if request.url.params["STOCK_TYPE"] == "1" else "688981"
            return httpx.Response(200, text=_sse_payload(code))
        if request.url.host == "yunhq.sse.com.cn":
            return httpx.Response(200, text=_kshare_payload())
        if request.url.host == "www.szse.cn":
            return httpx.Response(200, text=_szse_payload())
        if request.url.host == "www.bse.cn":
            return httpx.Response(200, text=_bse_payload())
        raise AssertionError(f"unexpected host: {request.url.host}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        batch = await OfficialStockMasterProvider(
            SseStockMasterProvider(client, policy=POLICY),
            SzseStockMasterProvider(client, policy=POLICY),
            BseStockMasterProvider(client, policy=POLICY),
        ).fetch()
    assert batch.expected_total == 4
    assert {record.exchange for record in batch.records} == {
        Exchange.SSE,
        Exchange.SZSE,
        Exchange.BSE,
    }


@pytest.mark.asyncio
async def test_official_provider_allows_optional_bse_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "query.sse.com.cn":
            assert request.url.params["pageHelp.cacheSize"] == "1"
            code = "600519" if request.url.params["STOCK_TYPE"] == "1" else "688981"
            return httpx.Response(200, text=_sse_payload(code))
        if request.url.host == "yunhq.sse.com.cn":
            return httpx.Response(200, text=_kshare_payload())
        if request.url.path.endswith("ShowReport/data"):
            return httpx.Response(200, text=_szse_payload())
        return httpx.Response(503, text="unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        batch = await OfficialStockMasterProvider(
            SseStockMasterProvider(client, policy=POLICY),
            SzseStockMasterProvider(client, policy=POLICY),
            BseStockMasterProvider(client, policy=POLICY),
        ).fetch()

    assert {record.exchange for record in batch.records} == {
        Exchange.SSE,
        Exchange.SZSE,
    }
    assert batch.degradations == (
        "optional_market_unavailable:bse:StockMasterProviderError",
    )


@pytest.mark.asyncio
async def test_official_provider_can_require_bse() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "query.sse.com.cn":
            code = "600519" if request.url.params["STOCK_TYPE"] == "1" else "688981"
            return httpx.Response(200, text=_sse_payload(code))
        if request.url.host == "yunhq.sse.com.cn":
            return httpx.Response(200, text=_kshare_payload())
        if request.url.path.endswith("ShowReport/data"):
            return httpx.Response(200, text=_szse_payload())
        return httpx.Response(503, text="unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OfficialStockMasterProvider(
            SseStockMasterProvider(client, policy=POLICY),
            SzseStockMasterProvider(client, policy=POLICY),
            BseStockMasterProvider(client, policy=POLICY),
            require_bse=True,
        )
        with pytest.raises(StockMasterProviderError, match="bse"):
            await provider.fetch()
