from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from pawe_api.contracts import DataQuality
from pawe_api.data.snapshot import (
    SnapshotRecord,
    SnapshotValidationError,
    freeze_snapshot,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
CUTOFF = datetime(2026, 7, 31, 15, 0, tzinfo=SHANGHAI)
LOCKED = datetime(2026, 8, 1, 9, 0, tzinfo=SHANGHAI)


def _record(**changes: object) -> SnapshotRecord:
    values = {
        "source": "tencent",
        "as_of": CUTOFF,
        "fetched_at": LOCKED,
        "quality": DataQuality.SINGLE_SOURCE,
        "payload": {"stock_code": "002472", "close": "38.42"},
        "published_at": None,
    }
    values.update(changes)
    return SnapshotRecord(**values)  # type: ignore[arg-type]


def test_snapshot_hash_is_reproducible() -> None:
    first = freeze_snapshot([_record()], cutoff=CUTOFF, locked_at=LOCKED)
    second = freeze_snapshot([_record()], cutoff=CUTOFF, locked_at=LOCKED)
    assert first.content_hash == second.content_hash


def test_snapshot_rejects_future_market_data() -> None:
    future = datetime(2026, 8, 3, 9, 30, tzinfo=SHANGHAI)
    with pytest.raises(SnapshotValidationError, match="exceeds"):
        freeze_snapshot([_record(as_of=future)], cutoff=CUTOFF, locked_at=LOCKED)


def test_date_only_publication_on_lock_date_is_delayed() -> None:
    with pytest.raises(SnapshotValidationError, match="next day"):
        freeze_snapshot(
            [_record(published_at=LOCKED.date())],
            cutoff=CUTOFF,
            locked_at=LOCKED,
        )


def test_post_cutoff_publication_can_be_locked_before_weekly_publication() -> None:
    frozen = freeze_snapshot(
        [_record(published_at=date(2026, 7, 31))],
        cutoff=CUTOFF,
        locked_at=LOCKED,
    )

    assert frozen.cutoff == CUTOFF


def test_snapshot_rejects_conflicted_record() -> None:
    with pytest.raises(SnapshotValidationError, match="conflicted"):
        freeze_snapshot([_record(quality=DataQuality.CONFLICTED)], cutoff=CUTOFF, locked_at=LOCKED)
