import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from time import monotonic
from typing import cast

import httpx

from pawe_api.data import eastmoney, sina, tencent
from pawe_api.data.official_stock_master import (
    OfficialStockMasterPage,
    parse_bse_stock_master_page,
    parse_sse_kshare_list,
    parse_sse_stock_master_page,
    parse_szse_stock_master_page,
)
from pawe_api.data.series import (
    ProviderDailySeries,
    ReconciledDailySeries,
    reconcile_daily_series,
)
from pawe_api.data.stock_master import (
    Exchange,
    StockMasterPage,
    StockMasterPayloadError,
    StockMasterRecord,
    parse_eastmoney_stock_master_page,
)

Sleep = Callable[[float], Awaitable[None]]
SinaFetcher = Callable[..., object]


class DailyProviderError(RuntimeError):
    def __init__(self, source: str, reason: str) -> None:
        super().__init__(f"{source}: {reason}")
        self.source = source
        self.reason = reason


class StockMasterProviderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StockMasterBatch:
    expected_total: int
    records: tuple[StockMasterRecord, ...]
    warnings: tuple[str, ...]
    degradations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderPolicy:
    timeout_seconds: float = 8.0
    retry_count: int = 2
    min_interval_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("provider timeout must be positive")
        if self.retry_count < 0:
            raise ValueError("provider retry count cannot be negative")
        if self.min_interval_seconds < 0:
            raise ValueError("provider interval cannot be negative")


class HostRateLimiter:
    def __init__(
        self,
        min_interval_seconds: float,
        *,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._interval = min_interval_seconds
        self._sleep = sleep
        self._clock = clock
        self._last_request_at: float | None = None
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = self._clock()
            if self._last_request_at is not None:
                remaining = self._interval - (now - self._last_request_at)
                if remaining > 0:
                    await self._sleep(remaining)
            self._last_request_at = self._clock()


class _HttpProvider:
    source: str

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        policy: ProviderPolicy | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._client = client
        self._policy = policy or ProviderPolicy()
        self._sleep = sleep
        self._limiter = HostRateLimiter(
            self._policy.min_interval_seconds,
            sleep=sleep,
        )

    async def _get_text(
        self,
        url: str,
        params: dict[str, str],
        *,
        headers: dict[str, str] | None = None,
    ) -> str:
        last_error = "request failed"
        for attempt in range(self._policy.retry_count + 1):
            await self._limiter.wait()
            try:
                response = await self._client.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self._policy.timeout_seconds,
                )
                response.raise_for_status()
                if not response.text.strip():
                    raise httpx.TransportError("empty response")
                return response.text
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_error = type(exc).__name__
                if attempt < self._policy.retry_count:
                    await self._sleep(0.25 * (2**attempt))
        raise DailyProviderError(self.source, last_error)

    async def _post_text(
        self,
        url: str,
        data: dict[str, str],
        *,
        headers: dict[str, str] | None = None,
    ) -> str:
        last_error = "request failed"
        for attempt in range(self._policy.retry_count + 1):
            await self._limiter.wait()
            try:
                response = await self._client.post(
                    url,
                    data=data,
                    headers=headers,
                    timeout=self._policy.timeout_seconds,
                )
                response.raise_for_status()
                if not response.text.strip():
                    raise httpx.TransportError("empty response")
                return response.text
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_error = type(exc).__name__
                if attempt < self._policy.retry_count:
                    await self._sleep(0.25 * (2**attempt))
        raise DailyProviderError(self.source, last_error)


class TencentDailyProvider(_HttpProvider):
    source = "tencent"
    endpoint = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

    async def fetch(self, stock_key: str, start: date, end: date) -> ProviderDailySeries:
        _validate_request(stock_key, start, end)
        fetched_at = datetime.now(UTC)
        payload = await self._get_text(
            self.endpoint,
            {"param": f"{stock_key},day,{start.isoformat()},{end.isoformat()},320,qfq"},
        )
        try:
            bars = tencent.parse_qfq_daily_bars(
                payload,
                stock_key=stock_key,
                start=start,
                end=end,
                fetched_at=fetched_at,
            )
            warnings: tuple[str, ...] = ()
            if not any(bar.trade_date == end for bar in bars):
                close_quote = tencent.parse_closed_quote_bar(
                    payload,
                    stock_key=stock_key,
                    trade_date=end,
                    fetched_at=fetched_at,
                )
                if close_quote is not None:
                    bars = tuple(sorted((*bars, close_quote), key=lambda bar: bar.trade_date))
                    warnings = ("same_day_close_quote_fallback",)
        except tencent.TencentPayloadError as exc:
            raise DailyProviderError(self.source, type(exc).__name__) from exc
        return ProviderDailySeries(
            stock_key,
            self.source,
            fetched_at,
            bars,
            warnings=warnings,
        )


class EastmoneyDailyProvider(_HttpProvider):
    source = "eastmoney"
    endpoint = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

    async def fetch(self, stock_key: str, start: date, end: date) -> ProviderDailySeries:
        _validate_request(stock_key, start, end)
        fetched_at = datetime.now(UTC)
        code = stock_key[2:]
        market = "0" if stock_key.startswith("sz") else "1"
        payload = await self._get_text(
            self.endpoint,
            {
                "secid": f"{market}.{code}",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57",
                "klt": "101",
                "fqt": "1",
                "beg": start.strftime("%Y%m%d"),
                "end": end.strftime("%Y%m%d"),
            },
        )
        try:
            bars = eastmoney.parse_qfq_daily_bars(
                payload,
                stock_key=stock_key,
                expected_code=code,
                start=start,
                end=end,
                fetched_at=fetched_at,
            )
        except eastmoney.EastmoneyPayloadError as exc:
            raise DailyProviderError(self.source, type(exc).__name__) from exc
        return ProviderDailySeries(stock_key, self.source, fetched_at, bars)


class SinaDailyProvider:
    source = "sina"

    def __init__(
        self,
        *,
        policy: ProviderPolicy | None = None,
        sleep: Sleep = asyncio.sleep,
        fetcher: SinaFetcher | None = None,
    ) -> None:
        self._policy = policy or ProviderPolicy(min_interval_seconds=2)
        self._limiter = HostRateLimiter(
            self._policy.min_interval_seconds,
            sleep=sleep,
        )
        self._fetcher = fetcher

    async def fetch(self, stock_key: str, start: date, end: date) -> ProviderDailySeries:
        _validate_request(stock_key, start, end)
        await self._limiter.wait()
        fetched_at = datetime.now(UTC)
        fetcher = self._fetcher or _load_akshare_sina_fetcher()
        try:
            frame = await asyncio.wait_for(
                asyncio.to_thread(
                    fetcher,
                    symbol=stock_key,
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                    adjust="qfq",
                ),
                timeout=self._policy.timeout_seconds,
            )
            bars = sina.parse_qfq_daily_rows(
                _frame_records(frame),
                stock_key=stock_key,
                start=start,
                end=end,
                fetched_at=fetched_at,
            )
        except (TimeoutError, sina.SinaPayloadError, ValueError) as exc:
            raise DailyProviderError(self.source, type(exc).__name__) from exc
        except Exception as exc:
            raise DailyProviderError(self.source, type(exc).__name__) from exc
        return ProviderDailySeries(
            stock_key,
            self.source,
            fetched_at,
            bars,
            warnings=("eastmoney_fallback",),
        )


def _load_akshare_sina_fetcher() -> SinaFetcher:
    import akshare

    return cast(SinaFetcher, akshare.stock_zh_a_daily)


def _frame_records(frame: object) -> Sequence[Mapping[str, object]]:
    converter = getattr(frame, "to_dict", None)
    if converter is None or not callable(converter):
        raise ValueError("Sina result is not a tabular frame")
    rows = converter(orient="records")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Sina result has an invalid record shape")
    return rows


class EastmoneyStockMasterProvider(_HttpProvider):
    source = "eastmoney"
    endpoint = "https://82.push2.eastmoney.com/api/qt/clist/get"

    async def fetch(self, *, page_size: int = 1000) -> StockMasterBatch:
        if not 1 <= page_size <= 5000:
            raise ValueError("stock master page_size must be between 1 and 5000")
        fetched_at = datetime.now(UTC)
        first_page = await self._fetch_page(1, page_size, fetched_at)
        pages = math.ceil(first_page.total / page_size)
        results = [first_page]
        for page_number in range(2, pages + 1):
            page = await self._fetch_page(page_number, page_size, fetched_at)
            if page.total != first_page.total:
                raise StockMasterProviderError(
                    "stock master total changed during pagination"
                )
            results.append(page)
        records = tuple(record for page in results for record in page.records)
        warnings = tuple(warning for page in results for warning in page.warnings)
        if len({(record.code, record.exchange) for record in records}) != len(records):
            raise StockMasterProviderError("stock master pages contain duplicates")
        if len(records) + len(warnings) != first_page.total:
            raise StockMasterProviderError("stock master pagination is incomplete")
        return StockMasterBatch(first_page.total, records, warnings)

    async def _fetch_page(
        self,
        page_number: int,
        page_size: int,
        fetched_at: datetime,
    ) -> StockMasterPage:
        try:
            payload = await self._get_text(
                self.endpoint,
                {
                    "pn": str(page_number),
                    "pz": str(page_size),
                    "po": "1",
                    "np": "1",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f12",
                    "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
                    "fields": "f12,f13,f14,f26,f100",
                },
            )
        except DailyProviderError as exc:
            raise StockMasterProviderError(f"request_failed:{exc.reason}") from exc
        try:
            return parse_eastmoney_stock_master_page(payload, fetched_at=fetched_at)
        except StockMasterPayloadError as exc:
            raise StockMasterProviderError(type(exc).__name__) from exc


class SseStockMasterProvider(_HttpProvider):
    source = "sse"
    endpoint = "https://query.sse.com.cn/sseQuery/commonQuery.do"
    referer = "https://www.sse.com.cn/assortment/stock/home/"
    kshare_endpoint = "https://yunhq.sse.com.cn:32042/v1/sh1/list/exchange/kshare"

    async def fetch(self, *, page_size: int = 100) -> StockMasterBatch:
        batches: list[StockMasterBatch] = []
        for stock_type in ("1", "8"):
            first = await self._fetch_page(stock_type, 1, page_size)
            pages = [first]
            for page_number in range(2, first.page_count + 1):
                page = await self._fetch_page(stock_type, page_number, page_size)
                if (page.total, page.page_count) != (first.total, first.page_count):
                    raise StockMasterProviderError("sse pagination metadata changed")
                pages.append(page)
            batches.append(_official_batch(pages, Exchange.SSE))
        records = tuple(record for batch in batches for record in batch.records)
        warnings = tuple(warning for batch in batches for warning in batch.warnings)
        expected_total = sum(batch.expected_total for batch in batches)
        if len({record.code for record in records}) != len(records):
            raise StockMasterProviderError("SSE stock types contain duplicates")
        try:
            kshare_payload = await self._get_text(
                self.kshare_endpoint,
                {
                    "select": "code,name",
                    "begin": "0",
                    "end": "5000",
                },
                headers={"Referer": "https://star.sse.com.cn/market/stocklist/"},
            )
            kshare = parse_sse_kshare_list(kshare_payload)
        except (DailyProviderError, StockMasterPayloadError) as exc:
            raise StockMasterProviderError(
                f"sse_kshare_failed:{type(exc).__name__}:{exc}"
            ) from exc
        star_codes = {record.code for record in records if record.board == "star"}
        kshare_codes = {code for code, _ in kshare.securities}
        if star_codes != kshare_codes:
            raise StockMasterProviderError(
                "sse star coverage conflict: "
                f"master={len(star_codes)} kshare={len(kshare_codes)} "
                f"missing={len(kshare_codes - star_codes)} "
                f"extra={len(star_codes - kshare_codes)}"
            )
        return StockMasterBatch(expected_total, records, warnings)

    async def _fetch_page(
        self,
        stock_type: str,
        page_number: int,
        page_size: int,
    ) -> OfficialStockMasterPage:
        try:
            payload = await self._get_text(
                self.endpoint,
                {
                    "sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L",
                    "STOCK_TYPE": stock_type,
                    "REG_PROVINCE": "",
                    "CSRC_CODE": "",
                    "STOCK_CODE": "",
                    "COMPANY_STATUS": "2,4,5,7,8",
                    "type": "inParams",
                    "isPagination": "true",
                    "pageHelp.cacheSize": "1",
                    "pageHelp.beginPage": str(page_number),
                    "pageHelp.pageSize": str(page_size),
                    "pageHelp.pageNo": str(page_number),
                },
                headers={"Referer": self.referer},
            )
            return parse_sse_stock_master_page(payload, fetched_at=datetime.now(UTC))
        except (DailyProviderError, StockMasterPayloadError) as exc:
            raise StockMasterProviderError(
                f"sse_page_{page_number}_failed:{type(exc).__name__}:{exc}"
            ) from exc


class SzseStockMasterProvider(_HttpProvider):
    source = "szse"
    endpoint = "https://www.szse.cn/api/report/ShowReport/data"
    referer = "https://www.szse.cn/market/product/stock/list/index.html"

    async def fetch(self) -> StockMasterBatch:
        first = await self._fetch_page(1)
        pages = [first]
        for page_number in range(2, first.page_count + 1):
            page = await self._fetch_page(page_number)
            if (page.total, page.page_count) != (first.total, first.page_count):
                raise StockMasterProviderError("szse pagination metadata changed")
            pages.append(page)
        return _official_batch(pages, Exchange.SZSE)

    async def _fetch_page(self, page_number: int) -> OfficialStockMasterPage:
        try:
            payload = await self._get_text(
                self.endpoint,
                {
                    "SHOWTYPE": "JSON",
                    "CATALOGID": "1110",
                    "TABKEY": "tab1",
                    "PAGENO": str(page_number),
                },
                headers={"Referer": self.referer},
            )
            return parse_szse_stock_master_page(payload, fetched_at=datetime.now(UTC))
        except (DailyProviderError, StockMasterPayloadError) as exc:
            raise StockMasterProviderError(
                f"szse_page_{page_number}_failed:{type(exc).__name__}:{exc}"
            ) from exc


class BseStockMasterProvider(_HttpProvider):
    source = "bse"
    endpoint = "https://www.bse.cn/nqxxController/nqxxCnzq.do"
    referer = "https://www.bse.cn/nq/listedcompany.html"

    async def fetch(self) -> StockMasterBatch:
        first = await self._fetch_page(0)
        pages = [first]
        for page_number in range(1, first.page_count):
            page = await self._fetch_page(page_number)
            if (page.total, page.page_count) != (first.total, first.page_count):
                raise StockMasterProviderError("bse pagination metadata changed")
            pages.append(page)
        return _official_batch(pages, Exchange.BSE)

    async def _fetch_page(self, page_number: int) -> OfficialStockMasterPage:
        try:
            payload = await self._post_text(
                self.endpoint,
                {
                    "page": str(page_number),
                    "typejb": "T",
                    "xxfcbj": "2",
                    "zqdm": "",
                    "sortfield": "xxzqdm",
                    "sorttype": "asc",
                },
                headers={"Referer": self.referer},
            )
            return parse_bse_stock_master_page(payload, fetched_at=datetime.now(UTC))
        except (DailyProviderError, StockMasterPayloadError) as exc:
            raise StockMasterProviderError(
                f"bse_page_{page_number}_failed:{type(exc).__name__}:{exc}"
            ) from exc


class OfficialStockMasterProvider:
    def __init__(
        self,
        sse: SseStockMasterProvider,
        szse: SzseStockMasterProvider,
        bse: BseStockMasterProvider,
        *,
        require_bse: bool = False,
    ) -> None:
        self._providers = (sse, szse, bse)
        self._required_sources = {sse.source, szse.source}
        if require_bse:
            self._required_sources.add(bse.source)

    async def fetch(self) -> StockMasterBatch:
        results = await asyncio.gather(
            *(provider.fetch() for provider in self._providers),
            return_exceptions=True,
        )
        required_failures = [
            provider.source
            for provider, result in zip(self._providers, results, strict=True)
            if isinstance(result, BaseException)
            and provider.source in self._required_sources
        ]
        if required_failures:
            raise StockMasterProviderError(
                "required stock master incomplete: " + ",".join(required_failures)
            )
        degradations = tuple(
            "optional_market_unavailable:"
            f"{provider.source}:{type(result).__name__}"
            for provider, result in zip(self._providers, results, strict=True)
            if isinstance(result, BaseException)
        )
        batches = tuple(result for result in results if isinstance(result, StockMasterBatch))
        records = tuple(record for batch in batches for record in batch.records)
        warnings = tuple(warning for batch in batches for warning in batch.warnings)
        expected_total = sum(batch.expected_total for batch in batches)
        if (
            len(batches) < len(self._required_sources)
            or len(records) + len(warnings) != expected_total
        ):
            raise StockMasterProviderError("official stock master coverage is incomplete")
        if len({(record.code, record.exchange) for record in records}) != len(records):
            raise StockMasterProviderError("official stock master contains duplicates")
        return StockMasterBatch(expected_total, records, warnings, degradations)


def _official_batch(
    pages: list[OfficialStockMasterPage],
    exchange: Exchange,
) -> StockMasterBatch:
    if not pages or any(page.exchange is not exchange for page in pages):
        raise StockMasterProviderError(f"{exchange.value} pages are missing")
    expected_total = pages[0].total
    records = tuple(record for page in pages for record in page.records)
    warnings = tuple(warning for page in pages for warning in page.warnings)
    if len(records) + len(warnings) != expected_total:
        raise StockMasterProviderError(
            f"{exchange.value} pagination is incomplete: "
            f"expected={expected_total} actual={len(records) + len(warnings)}"
        )
    if len({record.code for record in records}) != len(records):
        raise StockMasterProviderError(f"{exchange.value} pages contain duplicates")
    return StockMasterBatch(expected_total, records, warnings)


class DailySeriesGateway:
    def __init__(
        self,
        primary: TencentDailyProvider,
        backup: EastmoneyDailyProvider,
    ) -> None:
        self._primary = primary
        self._backup = backup

    async def fetch(self, stock_key: str, start: date, end: date) -> ReconciledDailySeries:
        results = await asyncio.gather(
            self._primary.fetch(stock_key, start, end),
            self._backup.fetch(stock_key, start, end),
            return_exceptions=True,
        )
        primary, primary_warning = _provider_result(results[0], "primary")
        backup, backup_warning = _provider_result(results[1], "backup")
        reconciled = reconcile_daily_series(primary, backup)
        requested_stock_key = reconciled.stock_key or stock_key
        return replace(
            reconciled,
            stock_key=requested_stock_key,
            warnings=primary_warning + backup_warning + reconciled.warnings,
        )


def _provider_result(
    result: ProviderDailySeries | BaseException,
    role: str,
) -> tuple[ProviderDailySeries | None, tuple[str, ...]]:
    if isinstance(result, ProviderDailySeries):
        return result, ()
    if isinstance(result, DailyProviderError):
        return None, (f"{role}_fetch_failed:{result.source}:{result.reason}",)
    return None, (f"{role}_fetch_failed:unexpected_error",)


def _validate_request(stock_key: str, start: date, end: date) -> None:
    if len(stock_key) != 8 or stock_key[:2] not in {"sh", "sz"} or not stock_key[2:].isdigit():
        raise ValueError("stock_key must use sh/sz plus a six-digit code")
    if start > end:
        raise ValueError("daily request start cannot exceed end")
