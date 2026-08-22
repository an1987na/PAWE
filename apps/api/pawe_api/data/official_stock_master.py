import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from html import unescape

from pawe_api.contracts import DataQuality
from pawe_api.data.stock_master import (
    Exchange,
    StockMasterPayloadError,
    StockMasterRecord,
    build_stock_master_record,
)


@dataclass(frozen=True, slots=True)
class OfficialStockMasterPage:
    exchange: Exchange
    total: int
    page_count: int
    records: tuple[StockMasterRecord, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SseKshareList:
    total: int
    securities: tuple[tuple[str, str], ...]


def parse_sse_kshare_list(payload_text: str) -> SseKshareList:
    payload = _json_object(payload_text, "sse kshare")
    total = _positive_int(payload.get("total"), "sse kshare total")
    rows = payload.get("list")
    if not isinstance(rows, list):
        raise StockMasterPayloadError("sse kshare rows are missing")
    securities: list[tuple[str, str]] = []
    for row in rows:
        if (
            not isinstance(row, list)
            or len(row) < 2
            or not isinstance(row[0], str)
            or not isinstance(row[1], str)
        ):
            raise StockMasterPayloadError("sse kshare row is invalid")
        securities.append((row[0], row[1].strip()))
    if len(securities) != total or len({code for code, _ in securities}) != total:
        raise StockMasterPayloadError("sse kshare coverage is incomplete")
    return SseKshareList(total, tuple(securities))


def parse_sse_stock_master_page(
    payload_text: str,
    *,
    fetched_at: datetime,
) -> OfficialStockMasterPage:
    payload = _json_object(payload_text, "sse")
    page_help = payload.get("pageHelp")
    rows = payload.get("result")
    if not isinstance(page_help, dict) or not isinstance(rows, list):
        raise StockMasterPayloadError("sse stock master response is malformed")
    total = _positive_int(page_help.get("total"), "sse total")
    page_count = _positive_int(page_help.get("pageCount"), "sse page count")
    return _parse_rows(
        rows,
        exchange=Exchange.SSE,
        total=total,
        page_count=page_count,
        fetched_at=fetched_at,
        source="sse",
        code_field="A_STOCK_CODE",
        name_field="SEC_NAME_CN",
        listing_date_field="LIST_DATE",
        industry_field="CSRC_CODE_DESC",
    )


def parse_szse_stock_master_page(
    payload_text: str,
    *,
    fetched_at: datetime,
) -> OfficialStockMasterPage:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise StockMasterPayloadError("szse stock master response is not valid JSON") from exc
    if not isinstance(payload, list):
        raise StockMasterPayloadError("szse stock master response is malformed")
    tab = next(
        (
            item
            for item in payload
            if isinstance(item, dict)
            and isinstance(item.get("metadata"), dict)
            and item["metadata"].get("tabkey") == "tab1"
        ),
        None,
    )
    if tab is None or not isinstance(tab.get("data"), list):
        raise StockMasterPayloadError("szse A-share tab is missing")
    metadata = tab["metadata"]
    assert isinstance(metadata, dict)
    total = _positive_int(metadata.get("recordcount"), "szse total")
    page_count = _positive_int(metadata.get("pagecount"), "szse page count")
    return _parse_rows(
        tab["data"],
        exchange=Exchange.SZSE,
        total=total,
        page_count=page_count,
        fetched_at=fetched_at,
        source="szse",
        code_field="agdm",
        name_field="agjc",
        listing_date_field="agssrq",
        industry_field="sshymc",
        strip_html_name=True,
    )


def parse_bse_stock_master_page(
    payload_text: str,
    *,
    fetched_at: datetime,
) -> OfficialStockMasterPage:
    payload = _json_object(payload_text, "bse")
    content = payload.get("content")
    if not isinstance(content, list) or not content or not isinstance(content[0], dict):
        raise StockMasterPayloadError("bse stock master response is malformed")
    body = content[0]
    rows = body.get("data")
    if not isinstance(rows, list):
        raise StockMasterPayloadError("bse stock master rows are missing")
    total = _positive_int(body.get("total"), "bse total")
    page_count_value = body.get("totalPages")
    page_count = (
        _positive_int(page_count_value, "bse page count")
        if page_count_value is not None
        else 1
    )
    return _parse_rows(
        rows,
        exchange=Exchange.BSE,
        total=total,
        page_count=page_count,
        fetched_at=fetched_at,
        source="bse",
        code_field="xxzqdm",
        name_field="xxzqjc",
        listing_date_field="ssrq",
        industry_field="sshymc",
    )


def _parse_rows(
    rows: list[object],
    *,
    exchange: Exchange,
    total: int,
    page_count: int,
    fetched_at: datetime,
    source: str,
    code_field: str,
    name_field: str,
    listing_date_field: str,
    industry_field: str | None = None,
    strip_html_name: bool = False,
) -> OfficialStockMasterPage:
    records: list[StockMasterRecord] = []
    warnings: list[str] = []
    for index, raw in enumerate(rows):
        try:
            if not isinstance(raw, dict):
                raise StockMasterPayloadError("row is not an object")
            name = str(raw.get(name_field, "")).strip()
            if strip_html_name:
                name = unescape(re.sub(r"<[^>]+>", "", name)).strip()
            industry_value = raw.get(industry_field) if industry_field else None
            industry = (
                str(industry_value).strip()
                if industry_value not in {None, "", "-"}
                else None
            )
            records.append(
                build_stock_master_record(
                    code=str(raw.get(code_field, "")),
                    exchange=exchange,
                    name=name,
                    listing_date=_iso_date(raw.get(listing_date_field)),
                    provider_industry=industry,
                    source=source,
                    quality=DataQuality.SINGLE_SOURCE,
                    fetched_at=fetched_at,
                )
            )
        except StockMasterPayloadError as exc:
            rejected_code = (
                str(raw.get(code_field, "unknown"))
                if isinstance(raw, dict)
                else "unknown"
            )
            warnings.append(f"row_{index}_{rejected_code}_rejected:{exc}")
    if not records:
        raise StockMasterPayloadError(f"{source} stock master response has no valid rows")
    if len({record.code for record in records}) != len(records):
        raise StockMasterPayloadError(f"{source} stock master page contains duplicates")
    return OfficialStockMasterPage(
        exchange,
        total,
        page_count,
        tuple(records),
        tuple(warnings),
    )


def _json_object(payload_text: str, source: str) -> dict[str, object]:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise StockMasterPayloadError(
            f"{source} stock master response is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise StockMasterPayloadError(f"{source} stock master response is malformed")
    return payload


def _positive_int(value: object, field: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise StockMasterPayloadError(f"{field} is invalid") from exc
    if parsed <= 0:
        raise StockMasterPayloadError(f"{field} is invalid")
    return parsed


def _iso_date(value: object) -> date:
    try:
        return date.fromisoformat(str(value).strip().replace("/", "-")[:10])
    except ValueError as exc:
        raise StockMasterPayloadError("listing date is invalid") from exc
