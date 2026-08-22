import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from pawe_api.contracts import DataQuality
from pawe_api.data.series import NormalizedDailyBar


class EastmoneyPayloadError(ValueError):
    pass


def parse_qfq_daily_bars(
    payload_text: str,
    *,
    stock_key: str,
    expected_code: str,
    start: date,
    end: date,
    fetched_at: datetime,
) -> tuple[NormalizedDailyBar, ...]:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise EastmoneyPayloadError("Eastmoney response is not valid JSON") from exc
    if payload.get("rc") != 0:
        raise EastmoneyPayloadError(f"Eastmoney response code is not zero: {payload.get('rc')!r}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise EastmoneyPayloadError("Eastmoney response does not contain data")
    if str(data.get("code")) != expected_code:
        raise EastmoneyPayloadError("Eastmoney response contains an unexpected stock code")
    raw_bars = data.get("klines")
    if not isinstance(raw_bars, list):
        raise EastmoneyPayloadError("Eastmoney response does not contain klines")

    bars: list[NormalizedDailyBar] = []
    for raw_row in raw_bars:
        if not isinstance(raw_row, str):
            raise EastmoneyPayloadError("Eastmoney kline row is not a string")
        row = raw_row.split(",")
        if len(row) < 7:
            raise EastmoneyPayloadError("Eastmoney kline row has an unexpected shape")
        try:
            trade_date = date.fromisoformat(row[0])
            open_price = Decimal(row[1])
            close_price = Decimal(row[2])
            high_price = Decimal(row[3])
            low_price = Decimal(row[4])
            volume = Decimal(row[5])
            amount = Decimal(row[6])
        except (ValueError, InvalidOperation) as exc:
            raise EastmoneyPayloadError("Eastmoney kline row contains invalid values") from exc
        if not start <= trade_date <= end:
            raise EastmoneyPayloadError("Eastmoney kline contains a date outside requested window")
        if min(open_price, high_price, low_price, close_price) <= 0:
            raise EastmoneyPayloadError("Eastmoney kline contains non-positive prices")
        if volume < 0 or amount < 0:
            raise EastmoneyPayloadError("Eastmoney kline contains negative volume or amount")
        if high_price < max(open_price, close_price, low_price):
            raise EastmoneyPayloadError("Eastmoney high price is inconsistent")
        if low_price > min(open_price, close_price, high_price):
            raise EastmoneyPayloadError("Eastmoney low price is inconsistent")
        bars.append(
            NormalizedDailyBar(
                stock_key=stock_key,
                trade_date=trade_date,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=volume,
                amount=amount,
                adjustment="qfq",
                source="eastmoney",
                fetched_at=fetched_at,
                quality=DataQuality.SINGLE_SOURCE,
            )
        )
    if not bars:
        raise EastmoneyPayloadError("Eastmoney klines is empty")
    if len({bar.trade_date for bar in bars}) != len(bars):
        raise EastmoneyPayloadError("Eastmoney klines contains duplicate dates")
    return tuple(sorted(bars, key=lambda bar: bar.trade_date))
