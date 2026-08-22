from dataclasses import dataclass
from datetime import date, datetime


class ReplayLeakageError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReplayWeek:
    week_id: date
    decision_cutoff: datetime
    snapshot_as_of: datetime
    classification_as_of: date
    arm: str = "default"


def validate_walk_forward(weeks: list[ReplayWeek]) -> tuple[ReplayWeek, ...]:
    ordered = sorted(weeks, key=lambda week: (week.week_id, week.arm))
    replay_keys = {(week.week_id, week.arm) for week in ordered}
    if len(replay_keys) != len(ordered):
        raise ReplayLeakageError("walk-forward input contains duplicate week arms")
    for week in ordered:
        if week.snapshot_as_of > week.decision_cutoff:
            raise ReplayLeakageError(f"snapshot exceeds cutoff for week {week.week_id}")
        if week.classification_as_of > week.decision_cutoff.date():
            raise ReplayLeakageError(f"classification uses future data for week {week.week_id}")
    return tuple(ordered)
