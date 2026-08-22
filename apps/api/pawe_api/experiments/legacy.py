import hashlib
import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path


class LegacyDocumentType(StrEnum):
    WEEKLY_SELECTION = "weekly_selection"
    WEEKLY_REVIEW = "weekly_review"
    SKIPPED_WEEK = "skipped_week"
    UNKNOWN = "unknown"


class LegacyParseQuality(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class LegacyBucket(StrEnum):
    MAIN = "main"
    RESERVE = "reserve"


@dataclass(frozen=True, slots=True)
class LegacySource:
    relative_path: str
    size_bytes: int
    modified_at: datetime
    sha256: str


@dataclass(frozen=True, slots=True)
class LegacyItem:
    bucket: LegacyBucket
    stock_code: str
    stock_name: str
    direction: str | None = None
    rank: int | None = None
    baseline_price: float | None = None
    target_return: float | None = None
    week_high_return: float | None = None
    close_return: float | None = None
    max_drawdown: float | None = None


@dataclass(frozen=True, slots=True)
class LegacyDocument:
    source: LegacySource
    document_type: LegacyDocumentType
    document_date: date | None
    rule_version: str | None
    linked_source_ref: str | None
    parse_quality: LegacyParseQuality
    verification_status: str
    items: tuple[LegacyItem, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LegacyBatch:
    manifest_hash: str
    documents: tuple[LegacyDocument, ...]


def inventory_legacy_markdown(root: Path) -> tuple[LegacySource, ...]:
    resolved_root = root.resolve(strict=True)
    output_root = (resolved_root / "outputs").resolve(strict=True)
    sources = [_source_from_path(path, resolved_root) for path in output_root.glob("*.md")]
    return tuple(sorted(sources, key=lambda source: source.relative_path))


def stage_legacy_markdown(root: Path) -> LegacyBatch:
    sources = inventory_legacy_markdown(root)
    documents = tuple(read_and_parse_legacy(root, source) for source in sources)
    linked_documents = _infer_review_links(documents)
    manifest_payload = "\n".join(
        f"{source.relative_path}\0{source.size_bytes}\0{source.sha256}" for source in sources
    )
    return LegacyBatch(
        manifest_hash=hashlib.sha256(manifest_payload.encode()).hexdigest(),
        documents=linked_documents,
    )


def read_and_parse_legacy(root: Path, source: LegacySource) -> LegacyDocument:
    resolved_root = root.resolve(strict=True)
    path = (resolved_root / source.relative_path).resolve(strict=True)
    if not path.is_relative_to(resolved_root):
        raise ValueError("legacy source path escapes the configured root")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != source.sha256:
        raise ValueError("legacy source hash changed after inventory")
    return parse_legacy_markdown(content.decode("utf-8"), source=source)


def parse_legacy_markdown(markdown: str, *, source: LegacySource) -> LegacyDocument:
    document_type = _document_type(source.relative_path)
    document_date = _document_date(source.relative_path)
    rule_version = _first_group(markdown, r"规则版本[：:]\s*([A-Za-z0-9_.-]+)")
    linked_source = _first_group(markdown, r"对应预选文件[：:]\s*`([^`]+)`")
    warnings: list[str] = []
    items: list[LegacyItem] = []

    if document_type in {
        LegacyDocumentType.WEEKLY_SELECTION,
        LegacyDocumentType.WEEKLY_REVIEW,
    }:
        items = _parse_weekly_tables(markdown, document_type, warnings)
        main_items = [item for item in items if item.bucket is LegacyBucket.MAIN]
        if not main_items:
            warnings.append("main_table_not_found")
        if document_type is LegacyDocumentType.WEEKLY_SELECTION and len(main_items) != 5:
            warnings.append(f"unexpected_main_count:{len(main_items)}")
        if document_type is LegacyDocumentType.WEEKLY_REVIEW and linked_source is None:
            warnings.append("linked_selection_not_declared")

    quality = _parse_quality(document_type, items, warnings)
    return LegacyDocument(
        source=source,
        document_type=document_type,
        document_date=document_date,
        rule_version=rule_version,
        linked_source_ref=linked_source,
        parse_quality=quality,
        verification_status="legacy_unverified",
        items=tuple(items),
        warnings=tuple(warnings),
    )


def _source_from_path(path: Path, root: Path) -> LegacySource:
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("legacy source path escapes the configured root")
    content = resolved.read_bytes()
    stat = resolved.stat()
    return LegacySource(
        relative_path=resolved.relative_to(root).as_posix(),
        size_bytes=len(content),
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _infer_review_links(documents: tuple[LegacyDocument, ...]) -> tuple[LegacyDocument, ...]:
    selections = [
        document
        for document in documents
        if document.document_type is LegacyDocumentType.WEEKLY_SELECTION
        and document.document_date is not None
    ]
    resolved: list[LegacyDocument] = []
    for document in documents:
        if (
            document.document_type is not LegacyDocumentType.WEEKLY_REVIEW
            or document.document_date is None
            or document.linked_source_ref is not None
        ):
            resolved.append(document)
            continue
        candidates = [
            selection
            for selection in selections
            if selection.document_date is not None
            and 0 < (document.document_date - selection.document_date).days <= 7
        ]
        if not candidates:
            resolved.append(document)
            continue
        selected = max(candidates, key=lambda candidate: candidate.document_date or date.min)
        warnings = tuple(
            warning for warning in document.warnings if warning != "linked_selection_not_declared"
        )
        resolved.append(
            replace(
                document,
                linked_source_ref=Path(selected.source.relative_path).name,
                parse_quality=LegacyParseQuality.PARTIAL,
                warnings=warnings + ("linked_selection_inferred",),
            )
        )
    return tuple(resolved)


def _document_type(relative_path: str) -> LegacyDocumentType:
    name = Path(relative_path).name
    if "周初预选" in name:
        return LegacyDocumentType.WEEKLY_SELECTION
    if "周终复盘" in name:
        return LegacyDocumentType.WEEKLY_REVIEW
    if "休市周跳过说明" in name:
        return LegacyDocumentType.SKIPPED_WEEK
    return LegacyDocumentType.UNKNOWN


def _document_date(relative_path: str) -> date | None:
    match = re.match(r"(\d{4}-\d{2}-\d{2})", Path(relative_path).name)
    return date.fromisoformat(match.group(1)) if match else None


def _parse_weekly_tables(
    markdown: str,
    document_type: LegacyDocumentType,
    warnings: list[str],
) -> list[LegacyItem]:
    lines = markdown.splitlines()
    items: list[LegacyItem] = []
    section = ""
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if line.startswith("##"):
            section = line.lstrip("#").strip()
        if line.startswith("|") and index + 1 < len(lines) and _is_separator(lines[index + 1]):
            header = _table_cells(line)
            rows: list[list[str]] = []
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(_table_cells(lines[index]))
                index += 1
            bucket = _table_bucket(section, document_type, header)
            if bucket is not None:
                for row in rows:
                    parsed = _parse_row(header, row, bucket, warnings)
                    if parsed is not None:
                        items.append(parsed)
            continue
        index += 1
    return items


def _table_bucket(
    section: str,
    document_type: LegacyDocumentType,
    header: list[str],
) -> LegacyBucket | None:
    if "代码" not in header or "名称" not in header:
        return None
    if "备选" in section:
        return LegacyBucket.RESERVE
    if document_type is LegacyDocumentType.WEEKLY_SELECTION and "主观察池" in section:
        return LegacyBucket.MAIN
    if document_type is LegacyDocumentType.WEEKLY_REVIEW and "本周结果" in section:
        return LegacyBucket.MAIN
    return None


def _parse_row(
    header: list[str],
    row: list[str],
    bucket: LegacyBucket,
    warnings: list[str],
) -> LegacyItem | None:
    values = dict(zip(header, row, strict=False))
    code = values.get("代码", "").strip()
    name = values.get("名称", "").strip()
    if not re.fullmatch(r"\d{6}", code) or not name:
        warnings.append(f"invalid_stock_row:{code or 'missing'}")
        return None
    return LegacyItem(
        bucket=bucket,
        stock_code=code,
        stock_name=name,
        direction=values.get("方向"),
        rank=_integer(values.get("序号")),
        baseline_price=_number(values.get("周一基准价") or values.get("周初基准价")),
        target_return=_percentage(values.get("预期目标")),
        week_high_return=_percentage(values.get("周内最高涨幅")),
        close_return=_percentage(values.get("周五收盘涨幅") or values.get("周终收盘涨幅")),
        max_drawdown=_percentage(values.get("周内最大回撤")),
    )


def _parse_quality(
    document_type: LegacyDocumentType,
    items: list[LegacyItem],
    warnings: list[str],
) -> LegacyParseQuality:
    if document_type in {LegacyDocumentType.SKIPPED_WEEK, LegacyDocumentType.UNKNOWN}:
        return (
            LegacyParseQuality.COMPLETE
            if document_type is LegacyDocumentType.SKIPPED_WEEK
            else LegacyParseQuality.FAILED
        )
    if not items:
        return LegacyParseQuality.FAILED
    return LegacyParseQuality.PARTIAL if warnings else LegacyParseQuality.COMPLETE


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator(line: str) -> bool:
    cells = _table_cells(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _first_group(value: str, pattern: str) -> str | None:
    match = re.search(pattern, value)
    return match.group(1) if match else None


def _integer(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() else None


def _percentage(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", value)
    return float(match.group(1)) / 100 if match else None


def _number(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"[+-]?\d+(?:\.\d+)?", value)
    return float(match.group()) if match else None
