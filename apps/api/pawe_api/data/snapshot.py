import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from pawe_api.contracts import DataQuality


class SnapshotValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SnapshotRecord:
    source: str
    as_of: datetime
    fetched_at: datetime
    quality: DataQuality
    payload: dict[str, Any]
    published_at: datetime | date | None = None


@dataclass(frozen=True, slots=True)
class FrozenSnapshot:
    cutoff: datetime
    locked_at: datetime
    content_hash: str
    records: tuple[SnapshotRecord, ...]


def freeze_snapshot(
    records: list[SnapshotRecord],
    *,
    cutoff: datetime,
    locked_at: datetime,
) -> FrozenSnapshot:
    _require_aware(cutoff, "cutoff")
    _require_aware(locked_at, "locked_at")
    if locked_at < cutoff:
        raise SnapshotValidationError("locked_at cannot be earlier than cutoff")
    if not records:
        raise SnapshotValidationError("snapshot requires at least one record")

    for record in records:
        _validate_record(record, cutoff, locked_at)

    canonical_records = [_canonical_record(record) for record in records]
    canonical_records.sort(key=lambda item: (item["source"], item["as_of"], item["payload_json"]))
    serialized = json.dumps(
        {"cutoff": cutoff.isoformat(), "records": canonical_records},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return FrozenSnapshot(
        cutoff=cutoff,
        locked_at=locked_at,
        content_hash=hashlib.sha256(serialized.encode()).hexdigest(),
        records=tuple(records),
    )


def _validate_record(
    record: SnapshotRecord,
    cutoff: datetime,
    locked_at: datetime,
) -> None:
    _require_aware(record.as_of, "record.as_of")
    _require_aware(record.fetched_at, "record.fetched_at")
    if record.as_of > cutoff:
        raise SnapshotValidationError("record as_of exceeds the decision cutoff")
    if record.as_of > record.fetched_at:
        raise SnapshotValidationError("record as_of cannot be later than fetched_at")
    if record.fetched_at > locked_at:
        raise SnapshotValidationError("record fetched_at exceeds the snapshot lock time")
    if record.quality in {DataQuality.CONFLICTED, DataQuality.MISSING}:
        raise SnapshotValidationError(
            "conflicted or missing records cannot enter a decision snapshot"
        )
    if isinstance(record.published_at, datetime):
        _require_aware(record.published_at, "record.published_at")
        if record.published_at > locked_at:
            raise SnapshotValidationError("record publication time exceeds the snapshot lock time")
    elif isinstance(record.published_at, date) and record.published_at >= locked_at.date():
        raise SnapshotValidationError(
            "date-only publication on the lock date is available from the next day"
        )


def _canonical_record(record: SnapshotRecord) -> dict[str, str]:
    published = record.published_at.isoformat() if record.published_at is not None else ""
    payload_json = json.dumps(
        record.payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return {
        "source": record.source,
        "as_of": record.as_of.isoformat(),
        "fetched_at": record.fetched_at.isoformat(),
        "quality": record.quality.value,
        "published_at": published,
        "payload_json": payload_json,
    }


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SnapshotValidationError(f"{field} must be timezone-aware")
