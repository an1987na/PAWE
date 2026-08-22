import json
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from pawe_api.contracts import DataQuality
from pawe_api.data.series import NormalizedDailyBar


class TencentPayloadError(ValueError):
    pass


def parse_qfq_daily_bars(
    payload_text: str,
    *,
    stock_key: str,
    start: date,
    end: date,
    fetched_at: datetime,
) -> tuple[NormalizedDailyBar, ...]:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise TencentPayloadError("Tencent response is not valid JSON") from exc
    if payload.get("code") != 0:
        raise TencentPayloadError(f"Tencent response code is not zero: {payload.get('code')!r}")

    stock_payload = payload.get("data", {}).get(stock_key)
    if not isinstance(stock_payload, dict):
        raise TencentPayloadError(f"Tencent response does not contain {stock_key}")
    raw_bars = stock_payload.get("qfqday")
    if not isinstance(raw_bars, list):
        raise TencentPayloadError("Tencent response does not contain qfqday")

    bars: list[NormalizedDailyBar] = []
    for row in raw_bars:
        if not isinstance(row, list) or len(row) < 6:
            raise TencentPayloadError("Tencent qfqday row has an unexpected shape")
        try:
            trade_date = date.fromisoformat(str(row[0]))
            open_price = Decimal(str(row[1]))
            close_price = Decimal(str(row[2]))
            high_price = Decimal(str(row[3]))
            low_price = Decimal(str(row[4]))
            volume = Decimal(str(row[5]))
        except (ValueError, InvalidOperation) as exc:
            raise TencentPayloadError("Tencent qfqday row contains invalid values") from exc
        if not start <= trade_date <= end:
            raise TencentPayloadError("Tencent qfqday contains a date outside the requested window")
        if min(open_price, high_price, low_price, close_price) <= 0 or volume < 0:
            raise TencentPayloadError(
                "Tencent qfqday contains non-positive prices or negative volume"
            )
        if high_price < max(open_price, close_price, low_price):
            raise TencentPayloadError("Tencent high price is inconsistent")
        if low_price > min(open_price, close_price, high_price):
            raise TencentPayloadError("Tencent low price is inconsistent")
        bars.append(
            NormalizedDailyBar(
                stock_key=stock_key,
                trade_date=trade_date,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=volume,
                adjustment="qfq",
                source="tencent",
                fetched_at=fetched_at,
                quality=DataQuality.SINGLE_SOURCE,
            )
        )

    if not bars:
        raise TencentPayloadError("Tencent qfqday is empty")
    if len({bar.trade_date for bar in bars}) != len(bars):
        raise TencentPayloadError("Tencent qfqday contains duplicate dates")
    return tuple(sorted(bars, key=lambda bar: bar.trade_date))


def parse_closed_quote_bar(
    payload_text: str,
    *,
    stock_key: str,
    trade_date: date,
    fetched_at: datetime,
) -> NormalizedDailyBar | None:
    """Read Tencent's same-day closed quote before qfqday has refreshed."""
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise TencentPayloadError("Tencent response is not valid JSON") from exc
    stock_payload = payload.get("data", {}).get(stock_key)
    quote_payload = stock_payload.get("qt") if isinstance(stock_payload, dict) else None
    quote = quote_payload.get(stock_key) if isinstance(quote_payload, dict) else None
    market = quote_payload.get("market") if isinstance(quote_payload, dict) else None
    if not isinstance(quote, list) or len(quote) < 38 or not isinstance(market, list):
        return None
    market_text = "|".join(str(item) for item in market)
    close_marker = "SH_close_已收盘" if stock_key.startswith("sh") else "SZ_close_已收盘"
    try:
        quote_at = datetime.strptime(str(quote[30]), "%Y%m%d%H%M%S")
    except ValueError as exc:
        raise TencentPayloadError("Tencent quote timestamp is invalid") from exc
    if (
        quote_at.date() != trade_date
        or quote_at.time() < time(15, 0)
        or close_marker not in market_text
    ):
        return None
    try:
        open_price = Decimal(str(quote[5]))
        close_price = Decimal(str(quote[3]))
        high_price = Decimal(str(quote[33]))
        low_price = Decimal(str(quote[34]))
        volume = Decimal(str(quote[36]))
        amount_parts = str(quote[35]).split("/")
        amount = Decimal(amount_parts[2]) if len(amount_parts) >= 3 else None
    except (InvalidOperation, IndexError) as exc:
        raise TencentPayloadError("Tencent quote contains invalid values") from exc
    if min(open_price, high_price, low_price, close_price) <= 0 or volume < 0:
        raise TencentPayloadError(
            "Tencent quote contains non-positive prices or negative volume"
        )
    if high_price < max(open_price, close_price, low_price):
        raise TencentPayloadError("Tencent quote high price is inconsistent")
    if low_price > min(open_price, close_price, high_price):
        raise TencentPayloadError("Tencent quote low price is inconsistent")
    return NormalizedDailyBar(
        stock_key=stock_key,
        trade_date=trade_date,
        open=open_price,
        high=high_price,
        low=low_price,
        close=close_price,
        volume=volume,
        amount=amount,
        adjustment="qfq",
        source="tencent",
        fetched_at=fetched_at,
        quality=DataQuality.SINGLE_SOURCE,
    )
