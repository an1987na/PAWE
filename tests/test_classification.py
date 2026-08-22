from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from pawe_api.contracts import DataQuality
from pawe_api.data.classification import (
    CAPCO_CLASSIFICATION_TYPE,
    OFFICIAL_THEME_TYPE,
    ClassificationError,
    ClassificationRecord,
    PaweSector,
    PrimaryClassificationStatus,
    parse_capco_tables,
    parse_official_theme_manifest,
    resolve_primary_classification,
    theme_manifest_records,
)
from pawe_api.rules.models import Domain

FETCHED_AT = datetime(2026, 8, 9, tzinfo=UTC)
CAPCO_URL = (
    "https://sp.capco.org.cn:82/file/202604/hangyefenlei/"
    "2025xiaban/2025xiabangupiaodaima.pdf"
)


def test_capco_table_parser_preserves_codes_and_does_not_guess_broad_industries() -> None:
    table = [
        ["2025年下半年上市公司行业分类结果", None, None, None, None, None, None, None],
        [
            "上市公司\n代码",
            "上市公司\n简称",
            "门类代码",
            "门类简称",
            "次类代码",
            "次类简称",
            "大类代码",
            "大类简称",
        ],
        ["000008", "神州高铁", "C", "制造业", "CG", "专用、通用", "37", "铁路运输设备"],
        ["000027", "深圳能源", "D", "电力、热力", "", "", "44", "电力生产和供应业"],
    ]
    records = parse_capco_tables(
        [table],
        valid_from=date(2026, 4, 3),
        published_at=date(2026, 4, 3),
        evidence_url=CAPCO_URL,
        fetched_at=FETCHED_AT,
    )
    assert [record.stock_code for record in records] == ["000008", "000027"]
    assert all(record.classification_type == CAPCO_CLASSIFICATION_TYPE for record in records)
    assert records[0].sector_code == "37"

    broad = resolve_primary_classification(
        "000008", (records[0],), as_of=date(2026, 8, 7)
    )
    energy = resolve_primary_classification(
        "000027", (records[1],), as_of=date(2026, 8, 7)
    )
    assert broad.status is PrimaryClassificationStatus.MISSING
    assert energy.primary is not None
    assert energy.primary.domain is Domain.MAIN
    assert energy.primary.sector_code == PaweSector.ENERGY.value
    assert energy.primary.published_at == date(2026, 4, 3)


def test_capco_cannot_be_used_before_its_publication() -> None:
    with pytest.raises(ClassificationError, match="before publication"):
        parse_capco_tables(
            [[[]]],
            valid_from=date(2026, 4, 2),
            published_at=date(2026, 4, 3),
            evidence_url=CAPCO_URL,
            fetched_at=FETCHED_AT,
        )


def test_official_theme_manifest_is_source_locked_and_domain_controlled() -> None:
    manifest = parse_official_theme_manifest(_theme_payload())
    records = theme_manifest_records(manifest, fetched_at=FETCHED_AT)
    assert len(records) == 2
    assert records[0].source == "csi:931071"
    assert records[0].classification_type == OFFICIAL_THEME_TYPE
    assert records[0].evidence_url == _theme_payload()["source_url"]

    wrong_host = _theme_payload(source_url="https://quote.eastmoney.com/center/")
    with pytest.raises(ClassificationError, match="approved official host"):
        parse_official_theme_manifest(wrong_host)

    wrong_domain = _theme_payload(domain="supplementary")
    with pytest.raises(ClassificationError, match="controlled sector"):
        parse_official_theme_manifest(wrong_domain)


def test_same_level_theme_conflict_is_excluded_instead_of_guessed() -> None:
    ai = _theme_record("000001", PaweSector.AI, "csi:931071")
    robotics = _theme_record("000001", PaweSector.ROBOTICS, "cni:980022")
    result = resolve_primary_classification(
        "000001", (ai, robotics), as_of=date(2026, 8, 7)
    )
    assert result.status is PrimaryClassificationStatus.CONFLICTED
    assert result.primary is None
    assert result.reasons == ("SAME_LEVEL_DOMAIN_CONFLICT:ai,robotics",)


def test_exact_capco_sector_precedes_theme_membership() -> None:
    theme = _theme_record("000027", PaweSector.AI, "csi:931071")
    industry = ClassificationRecord(
        stock_code="000027",
        classification_type=CAPCO_CLASSIFICATION_TYPE,
        label="电力、热力 / 电力生产和供应业",
        domain=None,
        sector_code="44",
        source="capco",
        quality=DataQuality.VERIFIED,
        valid_from=date(2026, 4, 3),
        valid_to=None,
        published_at=date(2026, 4, 3),
        evidence_url=CAPCO_URL,
        fetched_at=FETCHED_AT,
        content_hash="capco-hash",
    )
    result = resolve_primary_classification(
        "000027", (theme, industry), as_of=date(2026, 8, 7)
    )
    assert result.status is PrimaryClassificationStatus.READY
    assert result.primary is not None
    assert result.primary.sector_code == PaweSector.ENERGY.value


def test_future_and_degraded_evidence_do_not_grant_domain() -> None:
    record = _theme_record("000001", PaweSector.AI, "csi:931071")
    future = replace(record, valid_from=date(2026, 8, 8))
    degraded = replace(record, quality=DataQuality.DEGRADED)
    assert (
        resolve_primary_classification(
            "000001", (future,), as_of=date(2026, 8, 7)
        ).status
        is PrimaryClassificationStatus.MISSING
    )
    assert (
        resolve_primary_classification(
            "000001", (degraded,), as_of=date(2026, 8, 7)
        ).status
        is PrimaryClassificationStatus.MISSING
    )


def test_publication_and_fetch_boundaries_prevent_classification_leakage() -> None:
    record = _theme_record("000001", PaweSector.AI, "csi:931071")
    assert (
        resolve_primary_classification(
            "000001",
            (record,),
            as_of=date(2026, 8, 10),
            published_by=date(2026, 5, 28),
        ).status
        is PrimaryClassificationStatus.MISSING
    )
    assert (
        resolve_primary_classification(
            "000001",
            (record,),
            as_of=date(2026, 8, 10),
            published_by=date(2026, 8, 7),
            fetched_by=datetime(2026, 8, 8, tzinfo=UTC),
        ).status
        is PrimaryClassificationStatus.MISSING
    )


def _theme_payload(**changes: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "official-theme-1",
        "source": "csi",
        "index_code": "931071",
        "index_name": "中证人工智能产业指数",
        "domain": "main",
        "sector_code": "ai",
        "valid_from": "2026-06-15",
        "valid_to": None,
        "published_at": "2026-05-29",
        "source_url": "https://www.csindex.com.cn/zh-CN/indices/index-detail/931071",
        "constituents": [{"code": "000001"}, {"code": "600000"}],
    }
    payload.update(changes)
    return payload


def _theme_record(code: str, sector: PaweSector, source: str) -> ClassificationRecord:
    domain = (
        Domain.SUPPLEMENTARY
        if sector in {PaweSector.INNOVATIVE_DRUG, PaweSector.MEDICAL_DEVICE}
        else Domain.MAIN
    )
    return ClassificationRecord(
        stock_code=code,
        classification_type=OFFICIAL_THEME_TYPE,
        label=sector.value,
        domain=domain,
        sector_code=sector.value,
        source=source,
        quality=DataQuality.VERIFIED,
        valid_from=date(2026, 6, 15),
        valid_to=None,
        published_at=date(2026, 5, 29),
        evidence_url="https://www.csindex.com.cn/",
        fetched_at=FETCHED_AT,
        content_hash=f"hash-{source}",
    )
