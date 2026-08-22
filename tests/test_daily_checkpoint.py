from datetime import UTC, date, datetime

import pytest
from pawe_api.data.checkpoint import (
    CheckpointError,
    DailyIngestionCheckpoint,
    load_daily_checkpoint,
    save_daily_checkpoint,
)


def test_daily_checkpoint_round_trip_and_failure_retry(tmp_path) -> None:
    path = tmp_path / "daily.json"
    checkpoint = DailyIngestionCheckpoint(date(2026, 4, 1), date(2026, 8, 7))
    checkpoint.mark(
        "000001",
        error="eastmoney:timeout",
        updated_at=datetime(2026, 8, 9, 10, tzinfo=UTC),
    )
    checkpoint.mark(
        "000002",
        error=None,
        updated_at=datetime(2026, 8, 9, 10, 1, tzinfo=UTC),
    )
    checkpoint.mark(
        "000001",
        error=None,
        updated_at=datetime(2026, 8, 9, 10, 2, tzinfo=UTC),
    )
    save_daily_checkpoint(path, checkpoint)

    restored = load_daily_checkpoint(
        path,
        start=date(2026, 4, 1),
        end=date(2026, 8, 7),
    )
    assert restored.last_processed_code == "000002"
    assert restored.attempted_count == 3
    assert restored.failures == {}


def test_daily_checkpoint_rejects_another_window(tmp_path) -> None:
    path = tmp_path / "daily.json"
    save_daily_checkpoint(
        path,
        DailyIngestionCheckpoint(date(2026, 4, 1), date(2026, 8, 7)),
    )
    with pytest.raises(CheckpointError, match="window"):
        load_daily_checkpoint(
            path,
            start=date(2026, 4, 2),
            end=date(2026, 8, 7),
        )
