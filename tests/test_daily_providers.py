import json
from datetime import date
from decimal import Decimal

import httpx
import pytest
from pawe_api.contracts import DataQuality
from pawe_api.data.providers import (
    DailyProviderError,
    DailySeriesGateway,
    EastmoneyDailyProvider,
    ProviderPolicy,
    SinaDailyProvider,
    TencentDailyProvider,
)

pytestmark = pytest.mark.asyncio
POLICY = ProviderPolicy(timeout_seconds=1, retry_count=0, min_interval_seconds=0)


def _tencent_payload(stock_key: str) -> str:
    return json.dumps(
        {
            "code": 0,
            "data": {
                stock_key: {
                    "qfqday": [["2025-02-21", "18.14", "20.69", "20.69", "18.06", "100"]]
                }
            },
        }
    )


def _eastmoney_payload(code: str) -> str:
    return json.dumps(
        {
            "rc": 0,
            "data": {
                "code": code,
                "klines": ["2025-02-21,18.14,20.69,20.69,18.06,100,2000"],
            },
        }
    )


async def test_tencent_provider_builds_bounded_qfq_request() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["param"] == (
            "sz300383,day,2025-02-21,2025-02-28,320,qfq"
        )
        return httpx.Response(200, text=_tencent_payload("sz300383"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        series = await TencentDailyProvider(client, policy=POLICY).fetch(
            "sz300383", date(2025, 2, 21), date(2025, 2, 28)
        )
    assert series.source == "tencent"
    assert series.bars[0].trade_date == date(2025, 2, 21)


async def test_tencent_provider_uses_closed_quote_before_daily_refresh() -> None:
    quote = [""] * 38
    quote[3] = "13.13"
    quote[5] = "13.00"
    quote[30] = "20250822153000"
    quote[33] = "13.28"
    quote[34] = "12.99"
    quote[35] = "13.13/829165/1089594759"
    quote[36] = "829165"
    payload = json.dumps(
        {
            "code": 0,
            "data": {
                "sz300383": {
                    "qfqday": [
                        ["2025-08-21", "12.15", "13.14", "13.16", "12.04", "100"]
                    ],
                    "qt": {
                        "sz300383": quote,
                        "market": ["2025-08-22 15:30:00|SZ_close_已收盘"],
                    },
                }
            },
        },
        ensure_ascii=False,
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        series = await TencentDailyProvider(client, policy=POLICY).fetch(
            "sz300383", date(2025, 8, 21), date(2025, 8, 22)
        )

    assert series.bars[-1].trade_date == date(2025, 8, 22)
    assert series.bars[-1].close == Decimal("13.13")
    assert series.bars[-1].amount == Decimal("1089594759")
    assert series.warnings == ("same_day_close_quote_fallback",)


@pytest.mark.parametrize(
    ("stock_key", "expected_secid"),
    [("sz300383", "0.300383"), ("sh600519", "1.600519")],
)
async def test_eastmoney_provider_maps_exchange_to_secid(
    stock_key: str, expected_secid: str
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["secid"] == expected_secid
        assert request.url.params["fqt"] == "1"
        return httpx.Response(200, text=_eastmoney_payload(stock_key[2:]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        series = await EastmoneyDailyProvider(client, policy=POLICY).fetch(
            stock_key, date(2025, 2, 21), date(2025, 2, 28)
        )
    assert series.stock_key == stock_key


async def test_sina_provider_uses_bounded_qfq_request_and_preserves_amount() -> None:
    class Frame:
        def to_dict(self, *, orient: str) -> list[dict[str, object]]:
            assert orient == "records"
            return [
                {
                    "date": date(2025, 2, 21),
                    "open": 18.14,
                    "high": 20.69,
                    "low": 18.06,
                    "close": 20.69,
                    "volume": 100,
                    "amount": 2000,
                }
            ]

    def fetcher(**kwargs: str) -> Frame:
        assert kwargs == {
            "symbol": "sz300383",
            "start_date": "20250221",
            "end_date": "20250228",
            "adjust": "qfq",
        }
        return Frame()

    provider = SinaDailyProvider(policy=POLICY, fetcher=fetcher)
    series = await provider.fetch(
        "sz300383", date(2025, 2, 21), date(2025, 2, 28)
    )

    assert series.source == "sina"
    assert series.bars[0].amount == 2000
    assert series.warnings == ("eastmoney_fallback",)


async def test_provider_retries_empty_transport_response() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(200, text="")
        return httpx.Response(200, text=_tencent_payload("sz300383"))

    async def no_sleep(_seconds: float) -> None:
        return None

    policy = ProviderPolicy(timeout_seconds=1, retry_count=1, min_interval_seconds=0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await TencentDailyProvider(client, policy=policy, sleep=no_sleep).fetch(
            "sz300383", date(2025, 2, 21), date(2025, 2, 28)
        )
    assert attempts == 2


async def test_gateway_marks_backup_failure_without_discarding_primary() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "web.ifzq.gtimg.cn":
            return httpx.Response(200, text=_tencent_payload("sz300383"))
        return httpx.Response(200, text="")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await DailySeriesGateway(
            TencentDailyProvider(client, policy=POLICY),
            EastmoneyDailyProvider(client, policy=POLICY),
        ).fetch("sz300383", date(2025, 2, 21), date(2025, 2, 28))
    assert result.quality is DataQuality.SINGLE_SOURCE
    assert result.stock_key == "sz300383"
    assert any(warning.startswith("backup_fetch_failed:eastmoney") for warning in result.warnings)


async def test_gateway_marks_all_sources_missing() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await DailySeriesGateway(
            TencentDailyProvider(client, policy=POLICY),
            EastmoneyDailyProvider(client, policy=POLICY),
        ).fetch("sh600519", date(2025, 2, 21), date(2025, 2, 28))
    assert result.quality is DataQuality.MISSING
    assert result.stock_key == "sh600519"
    assert result.bars == ()
    assert len([warning for warning in result.warnings if "fetch_failed" in warning]) == 2


async def test_provider_request_validation_precedes_network() -> None:
    async with httpx.AsyncClient() as client:
        provider = TencentDailyProvider(client, policy=POLICY)
        with pytest.raises(ValueError, match="stock_key"):
            await provider.fetch("300383", date(2025, 2, 21), date(2025, 2, 28))
        with pytest.raises(ValueError, match="start"):
            await provider.fetch("sz300383", date(2025, 2, 28), date(2025, 2, 21))


async def test_invalid_provider_policy_is_rejected() -> None:
    with pytest.raises(ValueError, match="retry"):
        ProviderPolicy(retry_count=-1)
    error = DailyProviderError("tencent", "timeout")
    assert str(error) == "tencent: timeout"
