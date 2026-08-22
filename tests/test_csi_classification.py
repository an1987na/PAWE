from datetime import UTC, date, datetime

import httpx
import pytest
from pawe_api.data import csi_classification
from pawe_api.data.classification import ClassificationError, PaweSector
from pawe_api.data.csi_classification import (
    CSI_THEME_DEFINITIONS,
    CsiConstituentSheet,
    CsiThemeProvider,
    _constituent_file_url,
    parse_csi_constituent_xls,
)
from pawe_api.rules.models import Domain


class _FakeSheet:
    def __init__(self, rows: list[list[object]]) -> None:
        self._rows = rows
        self.nrows = len(rows)

    def row_values(self, index: int) -> list[object]:
        return self._rows[index]


class _FakeWorkbook:
    nsheets = 1

    def __init__(self, rows: list[list[object]]) -> None:
        self._sheet = _FakeSheet(rows)

    def sheet_by_index(self, index: int) -> _FakeSheet:
        assert index == 0
        return self._sheet


def test_csi_xls_parser_preserves_leading_zero_and_checks_one_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        [
            "日期Date",
            "指数代码 Index Code",
            "指数名称 Index Name",
            "成份券代码Constituent Code",
        ],
        ["20260807", "931071", "人工智能", 681.0],
        ["20260807", "931071", "人工智能", "600000"],
    ]
    monkeypatch.setattr(
        csi_classification.xlrd,
        "open_workbook",
        lambda **_: _FakeWorkbook(rows),
    )
    payload = bytes.fromhex("D0CF11E0A1B11AE1") + b"fixture"
    parsed = parse_csi_constituent_xls(payload, expected_index_code="931071")
    assert parsed.data_date == date(2026, 8, 7)
    assert parsed.constituent_codes == ("000681", "600000")


def test_csi_xls_parser_rejects_mixed_dates(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        [
            "日期Date",
            "指数代码 Index Code",
            "指数名称 Index Name",
            "成份券代码Constituent Code",
        ],
        ["20260807", "931071", "人工智能", "000681"],
        ["20260808", "931071", "人工智能", "600000"],
    ]
    monkeypatch.setattr(
        csi_classification.xlrd,
        "open_workbook",
        lambda **_: _FakeWorkbook(rows),
    )
    payload = bytes.fromhex("D0CF11E0A1B11AE1") + b"fixture"
    with pytest.raises(ClassificationError, match="mixes dates"):
        parse_csi_constituent_xls(payload, expected_index_code="931071")


def test_csi_material_url_is_locked_to_official_xls() -> None:
    payload = _material_payload("https://oss-ch.csindex.com.cn/path/931071cons.xls")
    assert _constituent_file_url(payload).endswith("931071cons.xls")
    with pytest.raises(ClassificationError, match="official host"):
        _constituent_file_url(_material_payload("https://example.com/cons.xls"))


@pytest.mark.asyncio
async def test_csi_provider_uses_fetch_date_as_first_replayable_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("index-details-data"):
            assert request.url.params["fileLang"] == "2"
            assert request.url.params["indexCode"] == "931071"
            return httpx.Response(
                200,
                json=_material_payload(
                    "https://oss-ch.csindex.com.cn/path/931071cons.xls"
                ),
            )
        return httpx.Response(200, content=b"workbook")

    monkeypatch.setattr(
        csi_classification,
        "parse_csi_constituent_xls",
        lambda *_args, **_kwargs: CsiConstituentSheet(
            data_date=date(2026, 8, 7),
            index_code="931071",
            index_name="人工智能",
            constituent_codes=("000681", "600000"),
        ),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        manifest = await CsiThemeProvider(client).fetch(
            CSI_THEME_DEFINITIONS["931071"],
            fetched_at=datetime(2026, 8, 9, 1, tzinfo=UTC),
        )
    assert manifest.published_at == date(2026, 8, 7)
    assert manifest.valid_from == date(2026, 8, 9)
    assert manifest.domain is Domain.MAIN
    assert manifest.sector is PaweSector.AI


def test_controlled_csi_theme_set_covers_v9_non_energy_sectors() -> None:
    assert {definition.sector for definition in CSI_THEME_DEFINITIONS.values()} == {
        PaweSector.AI,
        PaweSector.ROBOTICS,
        PaweSector.SEMICONDUCTOR,
        PaweSector.INNOVATIVE_DRUG,
        PaweSector.MEDICAL_DEVICE,
    }


def _material_payload(file_url: str) -> dict[str, object]:
    return {
        "code": "200",
        "data": {
            "样本列表": [
                {
                    "filePath": file_url,
                    "fileType": "xls",
                }
            ]
        },
    }
