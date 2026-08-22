from datetime import UTC, date, datetime

import pytest
from pawe_api.contracts import DataQuality
from pawe_api.data.repository import SnapshotInputRecord, _snapshot_quality
from pawe_api.data.snapshot import SnapshotRecord, SnapshotValidationError, freeze_snapshot


def test_snapshot_quality_uses_weakest_usable_record() -> None:
    records = [
        _record("verified", DataQuality.VERIFIED),
        _record("single", DataQuality.SINGLE_SOURCE),
    ]
    assert _snapshot_quality(records) is DataQuality.SINGLE_SOURCE

    records.append(_record("degraded", DataQuality.DEGRADED))
    assert _snapshot_quality(records) is DataQuality.DEGRADED


def test_snapshot_record_cutoff_blocks_future_data() -> None:
    cutoff = datetime(2026, 8, 7, 15, tzinfo=UTC)
    with pytest.raises(SnapshotValidationError, match="exceeds the decision cutoff"):
        freeze_snapshot(
            [
                SnapshotRecord(
                    source="fixture",
                    as_of=datetime(2026, 8, 7, 15, 1, tzinfo=UTC),
                    fetched_at=datetime(2026, 8, 9, 8, tzinfo=UTC),
                    quality=DataQuality.VERIFIED,
                    payload={"record_key": "future"},
                )
            ],
            cutoff=cutoff,
            locked_at=datetime(2026, 8, 9, 8, tzinfo=UTC),
        )


def test_snapshot_rejects_records_fetched_after_lock_time() -> None:
    cutoff = datetime(2026, 8, 7, 15, tzinfo=UTC)
    with pytest.raises(SnapshotValidationError, match="fetched_at"):
        freeze_snapshot(
            [
                SnapshotRecord(
                    source="fixture",
                    as_of=cutoff,
                    fetched_at=datetime(2026, 8, 9, 9, tzinfo=UTC),
                    quality=DataQuality.VERIFIED,
                    payload={"record_key": "late-fetch"},
                )
            ],
            cutoff=cutoff,
            locked_at=datetime(2026, 8, 9, 8, tzinfo=UTC),
        )


def _record(key: str, quality: DataQuality) -> SnapshotInputRecord:
    return SnapshotInputRecord(
        record_key=key,
        source="fixture",
        as_of=datetime(2026, 8, 7, 7, tzinfo=UTC),
        fetched_at=datetime(2026, 8, 9, 8, tzinfo=UTC),
        quality=quality,
        payload={"trade_date": date(2026, 8, 7).isoformat()},
    )
