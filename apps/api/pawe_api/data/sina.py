from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from pawe_api.contracts import DataQuality
from pawe_api.data.series import NormalizedDailyBar


class SinaPayloadError(ValueError):
    pass


def parse_qfq_daily_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    stock_key: str,
    start: date,
    end: date,
    fetched_at: datetime,
) -> tuple[NormalizedDailyBar, ...]:
    bars: list[NormalizedDailyBar] = []
    for row in rows:
        try:
            trade_date = _parse_date(row["date"])
            open_price = Decimal(str(row["open"]))
            high_price = Decimal(str(row["high"]))
            low_price = Decimal(str(row["low"]))
            close_price = Decimal(str(row["close"]))
            # Sina exposes shares while the existing Tencent/Eastmoney daily
            # adapters use board lots (手). Normalize before reconciliation.
            volume = Decimal(str(row["volume"])) / Decimal(100)
            amount = Decimal(str(row["amount"]))
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise SinaPayloadError("Sina daily row contains invalid values") from exc
        if not start <= trade_date <= end:
            raise SinaPayloadError("Sina daily row is outside the requested window")
        if min(open_price, high_price, low_price, close_price) <= 0:
            raise SinaPayloadError("Sina daily row contains non-positive prices")
        if volume < 0 or amount < 0:
            raise SinaPayloadError("Sina daily row contains negative volume or amount")
        if high_price < max(open_price, close_price, low_price):
            raise SinaPayloadError("Sina high price is inconsistent")
        if low_price > min(open_price, close_price, high_price):
            raise SinaPayloadError("Sina low price is inconsistent")
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
                source="sina",
                fetched_at=fetched_at,
                quality=DataQuality.SINGLE_SOURCE,
            )
        )
    if not bars:
        raise SinaPayloadError("Sina daily rows are empty")
    if len({bar.trade_date for bar in bars}) != len(bars):
        raise SinaPayloadError("Sina daily rows contain duplicate dates")
    return tuple(sorted(bars, key=lambda bar: bar.trade_date))


def _parse_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
