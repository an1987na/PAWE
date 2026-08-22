import pytest
from pawe_api.contracts import WeeklyStatus
from pawe_api.workflow import InvalidStateTransition, require_weekly_transition


def test_weekly_workflow_requires_approval_before_publish() -> None:
    require_weekly_transition(WeeklyStatus.AWAITING_APPROVAL, WeeklyStatus.APPROVED)
    require_weekly_transition(WeeklyStatus.APPROVED, WeeklyStatus.PUBLISHED)


def test_weekly_workflow_rejects_direct_publish() -> None:
    with pytest.raises(InvalidStateTransition, match="not allowed"):
        require_weekly_transition(WeeklyStatus.AWAITING_APPROVAL, WeeklyStatus.PUBLISHED)


def test_published_week_can_only_be_reviewed() -> None:
    require_weekly_transition(WeeklyStatus.PUBLISHED, WeeklyStatus.REVIEWED)
    with pytest.raises(InvalidStateTransition, match="not allowed"):
        require_weekly_transition(WeeklyStatus.PUBLISHED, WeeklyStatus.RULE_READY)
