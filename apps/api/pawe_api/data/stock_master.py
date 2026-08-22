import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from pawe_api.contracts import DataQuality


class StockMasterPayloadError(ValueError):
    pass


class Exchange(StrEnum):
    SSE = "SSE"
    SZSE = "SZSE"
    BSE = "BSE"


@dataclass(frozen=True, slots=True)
class StockMasterRecord:
    code: str
    exchange: Exchange
    board: str
    name: str
    listing_date: date
    status: str
    provider_industry: str | None
    source: str
    quality: DataQuality
    fetched_at: datetime
    content_hash: str


@dataclass(frozen=True, slots=True)
class StockMasterPage:
    total: int
    records: tuple[StockMasterRecord, ...]
    warnings: tuple[str, ...]


def parse_eastmoney_stock_master_page(
    payload_text: str,
    *,
    fetched_at: datetime,
) -> StockMasterPage:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise StockMasterPayloadError("stock master response is not valid JSON") from exc
    if payload.get("rc") != 0:
        raise StockMasterPayloadError("stock master response code is not zero")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("diff"), list):
        raise StockMasterPayloadError("stock master response does not contain rows")
    try:
        total = int(data["total"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StockMasterPayloadError("stock master response total is invalid") from exc
    records: list[StockMasterRecord] = []
    warnings: list[str] = []
    for index, raw in enumerate(data["diff"]):
        try:
            records.append(_parse_record(raw, fetched_at))
        except StockMasterPayloadError as exc:
            warnings.append(f"row_{index}_rejected:{exc}")
    if not records:
        raise StockMasterPayloadError("stock master response has no valid rows")
    if len({(item.code, item.exchange) for item in records}) != len(records):
        raise StockMasterPayloadError("stock master response contains duplicate securities")
    return StockMasterPage(total, tuple(records), tuple(warnings))


def _parse_record(raw: object, fetched_at: datetime) -> StockMasterRecord:
    if not isinstance(raw, dict):
        raise StockMasterPayloadError("row is not an object")
    code = str(raw.get("f12", ""))
    name = str(raw.get("f14", "")).strip()
    if len(code) != 6 or not code.isdigit() or not name or name == "-":
        raise StockMasterPayloadError("code or name is invalid")
    listing_date = _listing_date(raw.get("f26"))
    exchange = _exchange(code, raw.get("f13"))
    industry_value = raw.get("f100")
    industry = (
        str(industry_value).strip()
        if industry_value not in {None, "", "-"}
        else None
    )
    return build_stock_master_record(
        code=code,
        exchange=exchange,
        name=name,
        listing_date=listing_date,
        provider_industry=industry,
        source="eastmoney",
        quality=DataQuality.SINGLE_SOURCE,
        fetched_at=fetched_at,
    )


def build_stock_master_record(
    *,
    code: str,
    exchange: Exchange,
    name: str,
    listing_date: date,
    provider_industry: str | None,
    source: str,
    quality: DataQuality,
    fetched_at: datetime,
) -> StockMasterRecord:
    normalized_name = name.strip()
    if len(code) != 6 or not code.isdigit() or not normalized_name:
        raise StockMasterPayloadError("code or name is invalid")
    detected_exchange = _exchange(
        code,
        1 if exchange is Exchange.SSE else 0 if exchange is Exchange.SZSE else None,
    )
    if detected_exchange is not exchange:
        raise StockMasterPayloadError("code does not match the declared exchange")
    if listing_date > fetched_at.date():
        raise StockMasterPayloadError("listing date is after the fetch date")
    if not source:
        raise StockMasterPayloadError("source is required")
    normalized: dict[str, object] = {
        "code": code,
        "exchange": exchange.value,
        "board": _board(code, exchange),
        "name": normalized_name,
        "listing_date": listing_date.isoformat(),
        "status": _status(normalized_name),
        "provider_industry": provider_industry,
        "source": source,
    }
    serialized = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return StockMasterRecord(
        code=code,
        exchange=exchange,
        board=str(normalized["board"]),
        name=normalized_name,
        listing_date=listing_date,
        status=str(normalized["status"]),
        provider_industry=provider_industry,
        source=source,
        quality=quality,
        fetched_at=fetched_at,
        content_hash=hashlib.sha256(serialized.encode()).hexdigest(),
    )


def _listing_date(value: object) -> date:
    text = str(value)
    if len(text) != 8 or not text.isdigit():
        raise StockMasterPayloadError("listing date is invalid")
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError as exc:
        raise StockMasterPayloadError("listing date is invalid") from exc


def _exchange(code: str, market: object) -> Exchange:
    if code.startswith(("4", "8", "92")):
        return Exchange.BSE
    if market == 1 and code.startswith(("600", "601", "603", "605", "688", "689")):
        return Exchange.SSE
    if market == 0 and code.startswith(
        ("000", "001", "002", "003", "300", "301", "302")
    ):
        return Exchange.SZSE
    raise StockMasterPayloadError("code and market do not identify an A-share exchange")


def _board(code: str, exchange: Exchange) -> str:
    if exchange is Exchange.BSE:
        return "bse"
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("300", "301", "302")):
        return "gem"
    return "main"


def _status(name: str) -> str:
    normalized = name.upper().replace(" ", "")
    if "退" in name:
        return "delisting"
    if normalized.startswith(("*ST", "ST")):
        return "st"
    return "active"
