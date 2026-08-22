from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from pawe_api.experiments.replay import ReplayLeakageError, ReplayWeek, validate_walk_forward

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _week(week_id: date, snapshot_as_of: datetime, classification_as_of: date) -> ReplayWeek:
    return ReplayWeek(
        week_id=week_id,
        decision_cutoff=datetime(week_id.year, week_id.month, week_id.day, 9, 0, tzinfo=SHANGHAI),
        snapshot_as_of=snapshot_as_of,
        classification_as_of=classification_as_of,
    )


def test_walk_forward_sorts_weeks_and_accepts_past_data() -> None:
    first = _week(
        date(2026, 8, 3),
        datetime(2026, 7, 31, 15, 0, tzinfo=SHANGHAI),
        date(2026, 7, 31),
    )
    second = _week(
        date(2026, 8, 10),
        datetime(2026, 8, 7, 15, 0, tzinfo=SHANGHAI),
        date(2026, 8, 7),
    )
    assert validate_walk_forward([second, first]) == (first, second)


def test_walk_forward_rejects_future_snapshot() -> None:
    week = _week(
        date(2026, 8, 3),
        datetime(2026, 8, 3, 9, 30, tzinfo=SHANGHAI),
        date(2026, 7, 31),
    )
    with pytest.raises(ReplayLeakageError, match="snapshot exceeds cutoff"):
        validate_walk_forward([week])


def test_walk_forward_allows_parallel_arms_but_rejects_same_arm_duplicate() -> None:
    week = _week(
        date(2026, 7, 6),
        datetime(2026, 7, 3, 15, 0, tzinfo=SHANGHAI),
        date(2026, 7, 3),
    )
    new_rule = ReplayWeek(
        week.week_id,
        week.decision_cutoff,
        week.snapshot_as_of,
        week.classification_as_of,
        arm="new_rule",
    )
    old_rule = ReplayWeek(
        week.week_id,
        week.decision_cutoff,
        week.snapshot_as_of,
        week.classification_as_of,
        arm="old_rule",
    )
    assert validate_walk_forward([old_rule, new_rule]) == (new_rule, old_rule)
    with pytest.raises(ReplayLeakageError, match="duplicate week arms"):
        validate_walk_forward([new_rule, new_rule])
