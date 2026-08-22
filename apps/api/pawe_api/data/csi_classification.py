import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import xlrd

from pawe_api.data.classification import (
    ClassificationError,
    OfficialThemeManifest,
    PaweSector,
)
from pawe_api.rules.models import Domain


@dataclass(frozen=True, slots=True)
class CsiThemeDefinition:
    index_code: str
    index_name: str
    sector: PaweSector

    @property
    def domain(self) -> Domain:
        if self.sector in {PaweSector.INNOVATIVE_DRUG, PaweSector.MEDICAL_DEVICE}:
            return Domain.SUPPLEMENTARY
        return Domain.MAIN


CSI_THEME_DEFINITIONS = {
    definition.index_code: definition
    for definition in (
        CsiThemeDefinition("931071", "中证人工智能产业指数", PaweSector.AI),
        CsiThemeDefinition("H30590", "中证机器人指数", PaweSector.ROBOTICS),
        CsiThemeDefinition(
            "H30184", "中证全指半导体产品与设备指数", PaweSector.SEMICONDUCTOR
        ),
        CsiThemeDefinition("931152", "中证创新药产业指数", PaweSector.INNOVATIVE_DRUG),
        CsiThemeDefinition("H30217", "中证全指医疗器械指数", PaweSector.MEDICAL_DEVICE),
    )
}


@dataclass(frozen=True, slots=True)
class CsiConstituentSheet:
    data_date: date
    index_code: str
    index_name: str
    constituent_codes: tuple[str, ...]


class CsiThemeProvider:
    material_endpoint = (
        "https://www.csindex.com.cn/csindex-home/indexInfo/index-details-data"
    )

    def __init__(self, client: httpx.AsyncClient, *, timeout_seconds: float = 20) -> None:
        self._client = client
        self._timeout_seconds = timeout_seconds

    async def fetch(
        self,
        definition: CsiThemeDefinition,
        *,
        fetched_at: datetime,
    ) -> OfficialThemeManifest:
        response = await self._client.get(
            self.material_endpoint,
            params={"fileLang": "2", "indexCode": definition.index_code},
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise ClassificationError("CSI material response is not valid JSON") from exc
        file_url = _constituent_file_url(payload)
        file_response = await self._client.get(file_url, timeout=self._timeout_seconds)
        file_response.raise_for_status()
        sheet = parse_csi_constituent_xls(
            file_response.content,
            expected_index_code=definition.index_code,
        )
        local_date = fetched_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
        if sheet.data_date > local_date:
            raise ClassificationError("CSI constituent data date is in the future")
        return OfficialThemeManifest(
            source="csi",
            index_code=definition.index_code,
            index_name=definition.index_name,
            domain=definition.domain,
            sector=definition.sector,
            valid_from=local_date,
            valid_to=None,
            published_at=sheet.data_date,
            source_url=file_url,
            constituent_codes=sheet.constituent_codes,
        )


def parse_csi_constituent_xls(
    payload: bytes,
    *,
    expected_index_code: str,
) -> CsiConstituentSheet:
    if not payload.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        raise ClassificationError("CSI constituent payload is not an XLS workbook")
    try:
        workbook = xlrd.open_workbook(file_contents=payload)
    except xlrd.XLRDError as exc:
        raise ClassificationError("CSI constituent workbook cannot be opened") from exc
    if workbook.nsheets != 1:
        raise ClassificationError("CSI constituent workbook must contain one sheet")
    sheet = workbook.sheet_by_index(0)
    if sheet.nrows < 2:
        raise ClassificationError("CSI constituent workbook has no data rows")
    headers = [str(value).strip() for value in sheet.row_values(0)]
    columns = {
        "date": _find_header(headers, "日期Date"),
        "index_code": _find_header(headers, "指数代码 Index Code"),
        "index_name": _find_header(headers, "指数名称 Index Name"),
        "constituent_code": _find_header(headers, "成份券代码Constituent Code"),
    }
    dates: set[date] = set()
    names: set[str] = set()
    codes: list[str] = []
    for row_index in range(1, sheet.nrows):
        row = sheet.row_values(row_index)
        row_index_code = _cell_code(row[columns["index_code"]], width=None)
        if row_index_code != expected_index_code:
            raise ClassificationError("CSI workbook index code does not match the request")
        data_date = _cell_date(row[columns["date"]])
        constituent_code = _cell_code(row[columns["constituent_code"]], width=6)
        index_name = str(row[columns["index_name"]]).strip()
        if not index_name:
            raise ClassificationError("CSI workbook has an empty index name")
        dates.add(data_date)
        names.add(index_name)
        codes.append(constituent_code)
    if len(dates) != 1 or len(names) != 1:
        raise ClassificationError("CSI workbook mixes dates or index names")
    if len(codes) != len(set(codes)):
        raise ClassificationError("CSI workbook contains duplicate constituents")
    return CsiConstituentSheet(
        data_date=next(iter(dates)),
        index_code=expected_index_code,
        index_name=next(iter(names)),
        constituent_codes=tuple(sorted(codes)),
    )


def _constituent_file_url(payload: Any) -> str:
    if not isinstance(payload, dict) or str(payload.get("code")) != "200":
        raise ClassificationError("CSI material response is unsuccessful")
    data = payload.get("data")
    files = data.get("样本列表") if isinstance(data, dict) else None
    if not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], dict):
        raise ClassificationError("CSI material response has no unique constituent file")
    file_url = files[0].get("filePath")
    file_type = files[0].get("fileType")
    if not isinstance(file_url, str) or file_type != "xls":
        raise ClassificationError("CSI constituent file metadata is invalid")
    if not re.fullmatch(r"https://oss-ch\.csindex\.com\.cn/.+\.xls(?:\?.*)?", file_url):
        raise ClassificationError("CSI constituent file is not on the official host")
    return file_url


def _find_header(headers: list[str], expected: str) -> int:
    try:
        return headers.index(expected)
    except ValueError as exc:
        raise ClassificationError(f"CSI workbook is missing header: {expected}") from exc


def _cell_code(value: object, *, width: int | None) -> str:
    if isinstance(value, float) and value.is_integer():
        text = str(int(value))
    else:
        text = str(value).strip()
    if width is not None:
        text = text.zfill(width)
        if not re.fullmatch(rf"\d{{{width}}}", text):
            raise ClassificationError("CSI constituent code is invalid")
    elif not re.fullmatch(r"[A-Z]?\d{5,6}", text):
        raise ClassificationError("CSI index code is invalid")
    return text


def _cell_date(value: object) -> date:
    if isinstance(value, float) and value.is_integer():
        text = str(int(value))
    else:
        text = str(value).strip()
    if not re.fullmatch(r"\d{8}", text):
        raise ClassificationError("CSI constituent date is invalid")
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
