from collections.abc import Mapping

from pawe_api.contracts import WeeklyStatus

WEEKLY_TRANSITIONS: Mapping[WeeklyStatus, frozenset[WeeklyStatus]] = {
    WeeklyStatus.CREATED: frozenset({WeeklyStatus.SNAPSHOT_READY, WeeklyStatus.FAILED}),
    WeeklyStatus.SNAPSHOT_READY: frozenset({WeeklyStatus.RULE_READY, WeeklyStatus.FAILED}),
    WeeklyStatus.RULE_READY: frozenset(
        {WeeklyStatus.AI_READY, WeeklyStatus.AI_DEGRADED, WeeklyStatus.FAILED}
    ),
    WeeklyStatus.AI_READY: frozenset({WeeklyStatus.AWAITING_APPROVAL, WeeklyStatus.FAILED}),
    WeeklyStatus.AI_DEGRADED: frozenset({WeeklyStatus.AWAITING_APPROVAL, WeeklyStatus.FAILED}),
    WeeklyStatus.AWAITING_APPROVAL: frozenset({WeeklyStatus.APPROVED, WeeklyStatus.FAILED}),
    WeeklyStatus.APPROVED: frozenset({WeeklyStatus.PUBLISHED, WeeklyStatus.FAILED}),
    WeeklyStatus.PUBLISHED: frozenset({WeeklyStatus.REVIEWED}),
    WeeklyStatus.REVIEWED: frozenset(),
    WeeklyStatus.FAILED: frozenset(),
}


class InvalidStateTransition(ValueError):
    pass


def require_weekly_transition(current: WeeklyStatus, target: WeeklyStatus) -> None:
    if target not in WEEKLY_TRANSITIONS[current]:
        raise InvalidStateTransition(
            f"weekly transition {current.value} -> {target.value} is not allowed"
        )
