import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

from pawe_api.contracts import DataQuality
from pawe_api.rules.models import Domain


class ClassificationError(ValueError):
    pass


class PrimaryClassificationStatus(StrEnum):
    READY = "ready"
    MISSING = "missing"
    CONFLICTED = "conflicted"


class PaweSector(StrEnum):
    AI = "ai"
    ROBOTICS = "robotics"
    SEMICONDUCTOR = "semiconductor"
    ENERGY = "energy"
    INNOVATIVE_DRUG = "innovative_drug"
    MEDICAL_DEVICE = "medical_device"


@dataclass(frozen=True, slots=True)
class ClassificationRecord:
    stock_code: str
    classification_type: str
    label: str
    domain: Domain | None
    sector_code: str | None
    source: str
    quality: DataQuality
    valid_from: date
    valid_to: date | None
    published_at: date | None
    evidence_url: str | None
    fetched_at: datetime
    content_hash: str


@dataclass(frozen=True, slots=True)
class PrimaryClassification:
    stock_code: str
    domain: Domain
    sector_code: str
    label: str
    source: str
    quality: DataQuality
    valid_from: date
    published_at: date
    fetched_at: datetime
    content_hash: str


@dataclass(frozen=True, slots=True)
class PrimaryClassificationResult:
    stock_code: str
    status: PrimaryClassificationStatus
    primary: PrimaryClassification | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OfficialThemeManifest:
    source: str
    index_code: str
    index_name: str
    domain: Domain
    sector: PaweSector
    valid_from: date
    valid_to: date | None
    published_at: date
    source_url: str
    constituent_codes: tuple[str, ...]


CAPCO_SOURCE = "capco"
CAPCO_CLASSIFICATION_TYPE = "capco_industry"
OFFICIAL_THEME_TYPE = "official_theme"
PRIMARY_CLASSIFICATION_TYPE = "pawe_primary"
PRIMARY_SOURCE = "pawe-v9.0"

_CAPCO_HEADER = "上市公司\n代码"
_OFFICIAL_THEME_HOSTS = {
    "csi": ("csindex.com.cn",),
    "cni": ("cnindex.com.cn", "szse.cn"),
}
_SECTOR_DOMAIN = {
    PaweSector.AI: Domain.MAIN,
    PaweSector.ROBOTICS: Domain.MAIN,
    PaweSector.SEMICONDUCTOR: Domain.MAIN,
    PaweSector.ENERGY: Domain.MAIN,
    PaweSector.INNOVATIVE_DRUG: Domain.SUPPLEMENTARY,
    PaweSector.MEDICAL_DEVICE: Domain.SUPPLEMENTARY,
}

# CAPCO's two-digit categories are intentionally mapped only where the whole
# category belongs to one PAWE sector. Broad categories such as 35 and 39 must
# never be guessed into medical-device, robotics, AI, or semiconductor.
_CAPCO_EXACT_SECTORS = {
    "06": PaweSector.ENERGY,
    "07": PaweSector.ENERGY,
    "25": PaweSector.ENERGY,
    "44": PaweSector.ENERGY,
}


def parse_capco_pdf(
    payload: bytes,
    *,
    valid_from: date,
    published_at: date,
    evidence_url: str,
    fetched_at: datetime,
) -> tuple[ClassificationRecord, ...]:
    if not payload.startswith(b"%PDF"):
        raise ClassificationError("CAPCO payload is not a PDF")
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - exercised by deployment packaging
        raise RuntimeError("pdfplumber is required for CAPCO PDF ingestion") from exc

    with pdfplumber.open(BytesIO(payload)) as pdf:
        tables = [page.extract_table() or [] for page in pdf.pages]
    return parse_capco_tables(
        tables,
        valid_from=valid_from,
        published_at=published_at,
        evidence_url=evidence_url,
        fetched_at=fetched_at,
    )


def parse_capco_tables(
    tables: list[list[list[str | None]]],
    *,
    valid_from: date,
    published_at: date,
    evidence_url: str,
    fetched_at: datetime,
) -> tuple[ClassificationRecord, ...]:
    if published_at > valid_from:
        raise ClassificationError("CAPCO data cannot be effective before publication")
    host = (urlparse(evidence_url).hostname or "").lower()
    if host != "capco.org.cn" and not host.endswith(".capco.org.cn"):
        raise ClassificationError("CAPCO evidence_url is not an official host")
    records: dict[str, ClassificationRecord] = {}
    for table in tables:
        for row in table:
            if len(row) < 8 or row[0] in {None, _CAPCO_HEADER}:
                continue
            code = _compact(row[0])
            if not re.fullmatch(r"\d{6}", code):
                continue
            category_code = _compact(row[6])
            category_label = _clean_text(row[7])
            division_code = _compact(row[2])
            division_label = _clean_text(row[3])
            subclass_code = _compact(row[4])
            subclass_label = _clean_text(row[5])
            if not category_code or not category_label or not division_code:
                raise ClassificationError(f"CAPCO row has missing classification: {code}")
            label = " / ".join(
                part
                for part in (division_label, subclass_label, category_label)
                if part
            )
            payload = {
                "stock_code": code,
                "classification_type": CAPCO_CLASSIFICATION_TYPE,
                "division_code": division_code,
                "subclass_code": subclass_code or None,
                "category_code": category_code,
                "label": label,
                "valid_from": valid_from.isoformat(),
                "published_at": published_at.isoformat(),
                "evidence_url": evidence_url,
            }
            record = ClassificationRecord(
                stock_code=code,
                classification_type=CAPCO_CLASSIFICATION_TYPE,
                label=label,
                domain=None,
                sector_code=category_code,
                source=CAPCO_SOURCE,
                quality=DataQuality.VERIFIED,
                valid_from=valid_from,
                valid_to=None,
                published_at=published_at,
                evidence_url=evidence_url,
                fetched_at=fetched_at,
                content_hash=_payload_hash(payload),
            )
            previous = records.get(code)
            if previous is not None and previous.content_hash != record.content_hash:
                raise ClassificationError(f"CAPCO contains conflicting rows: {code}")
            records[code] = record
    if not records:
        raise ClassificationError("CAPCO PDF contains no classification rows")
    return tuple(records[code] for code in sorted(records))


def parse_official_theme_manifest(
    payload: dict[str, Any],
) -> OfficialThemeManifest:
    schema_version = _required_string(payload, "schema_version")
    if schema_version != "official-theme-1":
        raise ClassificationError("unsupported official theme schema")
    source = _required_string(payload, "source").lower()
    if source not in _OFFICIAL_THEME_HOSTS:
        raise ClassificationError("theme source must be csi or cni")
    index_code = _required_string(payload, "index_code")
    index_name = _required_string(payload, "index_name")
    try:
        domain = Domain(_required_string(payload, "domain"))
        sector = PaweSector(_required_string(payload, "sector_code"))
        valid_from = date.fromisoformat(_required_string(payload, "valid_from"))
        published_at = date.fromisoformat(_required_string(payload, "published_at"))
        valid_to_value = payload.get("valid_to")
        valid_to = (
            date.fromisoformat(str(valid_to_value))
            if valid_to_value not in {None, ""}
            else None
        )
    except (TypeError, ValueError) as exc:
        raise ClassificationError("invalid theme manifest enum or date") from exc
    if domain is not _SECTOR_DOMAIN[sector]:
        raise ClassificationError("theme domain does not match the controlled sector")
    if published_at > valid_from:
        raise ClassificationError("theme publication cannot be after its effective date")
    if valid_to is not None and valid_to < valid_from:
        raise ClassificationError("theme valid_to precedes valid_from")
    source_url = _required_string(payload, "source_url")
    host = (urlparse(source_url).hostname or "").lower()
    allowed_hosts = _OFFICIAL_THEME_HOSTS[source]
    if not any(
        host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts
    ):
        raise ClassificationError("theme source_url is not an approved official host")
    raw_constituents = payload.get("constituents")
    if not isinstance(raw_constituents, list) or not raw_constituents:
        raise ClassificationError("theme constituents must be a non-empty list")
    codes: list[str] = []
    for item in raw_constituents:
        code = item.get("code") if isinstance(item, dict) else item
        code_text = str(code)
        if not re.fullmatch(r"\d{6}", code_text):
            raise ClassificationError("theme constituent codes must have six digits")
        codes.append(code_text)
    if len(codes) != len(set(codes)):
        raise ClassificationError("theme constituent codes must be unique")
    return OfficialThemeManifest(
        source=source,
        index_code=index_code,
        index_name=index_name,
        domain=domain,
        sector=sector,
        valid_from=valid_from,
        valid_to=valid_to,
        published_at=published_at,
        source_url=source_url,
        constituent_codes=tuple(sorted(codes)),
    )


def theme_manifest_records(
    manifest: OfficialThemeManifest,
    *,
    fetched_at: datetime,
) -> tuple[ClassificationRecord, ...]:
    source = f"{manifest.source}:{manifest.index_code}"
    records = []
    for code in manifest.constituent_codes:
        payload = {
            "stock_code": code,
            "classification_type": OFFICIAL_THEME_TYPE,
            "index_code": manifest.index_code,
            "index_name": manifest.index_name,
            "domain": manifest.domain.value,
            "sector_code": manifest.sector.value,
            "valid_from": manifest.valid_from.isoformat(),
            "valid_to": manifest.valid_to.isoformat() if manifest.valid_to else None,
            "published_at": manifest.published_at.isoformat(),
            "source_url": manifest.source_url,
        }
        records.append(
            ClassificationRecord(
                stock_code=code,
                classification_type=OFFICIAL_THEME_TYPE,
                label=manifest.index_name,
                domain=manifest.domain,
                sector_code=manifest.sector.value,
                source=source,
                quality=DataQuality.VERIFIED,
                valid_from=manifest.valid_from,
                valid_to=manifest.valid_to,
                published_at=manifest.published_at,
                evidence_url=manifest.source_url,
                fetched_at=fetched_at,
                content_hash=_payload_hash(payload),
            )
        )
    return tuple(records)


def resolve_primary_classification(
    stock_code: str,
    records: tuple[ClassificationRecord, ...],
    *,
    as_of: date,
    published_by: date | None = None,
    fetched_by: datetime | None = None,
) -> PrimaryClassificationResult:
    information_date = published_by or as_of
    usable = [
        record
        for record in records
        if record.stock_code == stock_code
        and record.valid_from <= as_of
        and (record.valid_to is None or record.valid_to >= as_of)
        and record.published_at is not None
        and record.published_at <= information_date
        and (fetched_by is None or record.fetched_at <= fetched_by)
        and record.quality in {DataQuality.VERIFIED, DataQuality.SINGLE_SOURCE}
    ]
    candidates: list[tuple[int, Domain, str, ClassificationRecord]] = []
    for record in usable:
        if record.classification_type == CAPCO_CLASSIFICATION_TYPE:
            sector = _CAPCO_EXACT_SECTORS.get(record.sector_code or "")
            if sector is not None:
                candidates.append((300, _SECTOR_DOMAIN[sector], sector.value, record))
        elif (
            record.classification_type == OFFICIAL_THEME_TYPE
            and record.source.split(":", maxsplit=1)[0] in _OFFICIAL_THEME_HOSTS
            and record.domain is not None
            and record.sector_code in {sector.value for sector in PaweSector}
        ):
            candidates.append((200, record.domain, record.sector_code, record))
    if not candidates:
        return PrimaryClassificationResult(
            stock_code,
            PrimaryClassificationStatus.MISSING,
            None,
            ("NO_APPROVED_DOMAIN_EVIDENCE",),
        )
    strongest = max(item[0] for item in candidates)
    winners = {(domain, sector) for rank, domain, sector, _ in candidates if rank == strongest}
    if len(winners) != 1:
        sectors = ",".join(sorted(sector for _, sector in winners))
        return PrimaryClassificationResult(
            stock_code,
            PrimaryClassificationStatus.CONFLICTED,
            None,
            (f"SAME_LEVEL_DOMAIN_CONFLICT:{sectors}",),
        )
    domain, sector_code = next(iter(winners))
    winner_records = [
        record
        for rank, candidate_domain, candidate_sector, record in candidates
        if rank == strongest
        and candidate_domain is domain
        and candidate_sector == sector_code
    ]
    sources = sorted(record.source for record in winner_records)
    effective_from = max(record.valid_from for record in winner_records)
    published_at = max(
        record.published_at for record in winner_records if record.published_at is not None
    )
    fetched_at = max(record.fetched_at for record in winner_records)
    payload = {
        "stock_code": stock_code,
        "domain": domain.value,
        "sector_code": sector_code,
        "source_evidence": sources,
        "valid_from": effective_from.isoformat(),
    }
    primary = PrimaryClassification(
        stock_code=stock_code,
        domain=domain,
        sector_code=sector_code,
        label=sector_code,
        source=PRIMARY_SOURCE,
        quality=DataQuality.VERIFIED,
        valid_from=effective_from,
        published_at=published_at,
        fetched_at=fetched_at,
        content_hash=_payload_hash(payload),
    )
    return PrimaryClassificationResult(
        stock_code,
        PrimaryClassificationStatus.READY,
        primary,
        (),
    )


def _compact(value: str | None) -> str:
    return "" if value is None else re.sub(r"\s+", "", value)


def _clean_text(value: str | None) -> str:
    return "" if value is None else re.sub(r"\s+", "", value).strip()


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ClassificationError(f"{key} must be a non-empty string")
    return value.strip()


def _payload_hash(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def utc_now() -> datetime:
    return datetime.now(UTC)
