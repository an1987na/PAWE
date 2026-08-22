import uuid
from datetime import UTC, date, datetime

import pytest
from pawe_api.decisions.ledger import (
    ApprovalAction,
    ApprovalCommand,
    DecisionConflictError,
    DecisionItemRecord,
    DecisionLedger,
    DecisionSetRecord,
    DecisionStatus,
    DecisionType,
    DecisionValidationError,
)

WEEK_ID = date(2026, 8, 3)
NOW = datetime(2026, 8, 4, 8, 30, tzinfo=UTC)


def _source(decision_type: DecisionType = DecisionType.AI, version: int = 1):
    return DecisionSetRecord(
        id=uuid.uuid4(),
        week_id=WEEK_ID,
        decision_type=decision_type,
        version=version,
        status=DecisionStatus.AWAITING_APPROVAL,
        fingerprint=f"source-{decision_type}-{version}",
        items=tuple(
            DecisionItemRecord(f"00000{index}", f"样本{index}", index) for index in range(1, 6)
        ),
        source_type=None,
        source_version=None,
        is_active=True,
        created_at=NOW,
    )


def _command(
    action: ApprovalAction = ApprovalAction.ACCEPT_AI,
    codes: tuple[str, ...] = ("000001", "000002", "000003", "000004", "000005"),
    key: str = "approval-key-001",
) -> ApprovalCommand:
    return ApprovalCommand(
        action=action,
        decision_version=1,
        selected_codes=codes,
        reason="确认本周结构化名单",
        idempotency_key=key,
    )


def _names() -> dict[str, str]:
    return {f"00000{index}": f"样本{index}" for index in range(1, 7)}


def test_accept_ai_then_publish_is_versioned_and_idempotent() -> None:
    ledger = DecisionLedger()
    ledger.add_source(_source())
    outcome = ledger.approve(
        week_id=WEEK_ID,
        source_type=DecisionType.AI,
        command=_command(),
        allowed_codes=set(_names()),
        stock_names=_names(),
        created_at=NOW,
    )
    assert outcome.approved_decision is not None
    assert outcome.approved_decision.status is DecisionStatus.APPROVED
    assert outcome.approved_decision.source_type is DecisionType.AI

    repeated = ledger.approve(
        week_id=WEEK_ID,
        source_type=DecisionType.AI,
        command=_command(),
        allowed_codes=set(_names()),
        stock_names=_names(),
        created_at=NOW,
    )
    assert repeated == outcome

    published = ledger.publish(
        week_id=WEEK_ID,
        decision_version=outcome.approved_decision.version,
        idempotency_key="publish-key-001",
    )
    assert published.status is DecisionStatus.PUBLISHED
    assert (
        ledger.publish(
            week_id=WEEK_ID,
            decision_version=published.version,
            idempotency_key="publish-key-001",
        )
        == published
    )


def test_manual_modification_preserves_capacity_and_eligible_pool() -> None:
    ledger = DecisionLedger()
    ledger.add_source(_source())
    modified_codes = ("000001", "000002", "000003", "000004", "000006")
    with pytest.raises(DecisionValidationError, match="preserve decision capacity"):
        ledger.approve(
            week_id=WEEK_ID,
            source_type=DecisionType.AI,
            command=_command(
                ApprovalAction.MODIFY,
                modified_codes[:-1],
                key="approval-key-short",
            ),
            allowed_codes=set(_names()),
            stock_names=_names(),
            created_at=NOW,
        )

    outcome = ledger.approve(
        week_id=WEEK_ID,
        source_type=DecisionType.AI,
        command=_command(ApprovalAction.MODIFY, modified_codes),
        allowed_codes=set(_names()),
        stock_names=_names(),
        created_at=NOW,
    )
    assert outcome.approved_decision is not None
    assert tuple(item.stock_code for item in outcome.approved_decision.items) == modified_codes


def test_rejection_records_audit_without_creating_publishable_set() -> None:
    ledger = DecisionLedger()
    ledger.add_source(_source())
    outcome = ledger.approve(
        week_id=WEEK_ID,
        source_type=DecisionType.AI,
        command=_command(ApprovalAction.REJECT, (), key="approval-key-reject"),
        allowed_codes=set(_names()),
        stock_names=_names(),
        created_at=NOW,
    )
    assert outcome.approval.action is ApprovalAction.REJECT
    assert outcome.approved_decision is None


def test_stale_version_and_idempotency_reuse_are_conflicts() -> None:
    ledger = DecisionLedger()
    ledger.add_source(_source())
    stale = ApprovalCommand(
        action=ApprovalAction.ACCEPT_AI,
        decision_version=2,
        selected_codes=_command().selected_codes,
        reason="旧页面提交",
        idempotency_key="approval-key-stale",
    )
    with pytest.raises(DecisionConflictError, match="stale"):
        ledger.approve(
            week_id=WEEK_ID,
            source_type=DecisionType.AI,
            command=stale,
            allowed_codes=set(_names()),
            stock_names=_names(),
            created_at=NOW,
        )


def test_source_cannot_be_approved_twice_with_different_keys() -> None:
    ledger = DecisionLedger()
    ledger.add_source(_source())
    ledger.approve(
        week_id=WEEK_ID,
        source_type=DecisionType.AI,
        command=_command(),
        allowed_codes=set(_names()),
        stock_names=_names(),
        created_at=NOW,
    )

    with pytest.raises(DecisionValidationError, match="not awaiting approval"):
        ledger.approve(
            week_id=WEEK_ID,
            source_type=DecisionType.AI,
            command=_command(key="approval-key-002"),
            allowed_codes=set(_names()),
            stock_names=_names(),
            created_at=NOW,
        )

    decisions = ledger.decisions_for_week(WEEK_ID)
    assert sum(item.decision_type is DecisionType.PUBLISHED for item in decisions) == 1

    ledger.approve(
        week_id=WEEK_ID,
        source_type=DecisionType.AI,
        command=_command(),
        allowed_codes=set(_names()),
        stock_names=_names(),
        created_at=NOW,
    )
    reused = ApprovalCommand(
        action=ApprovalAction.REJECT,
        decision_version=1,
        selected_codes=(),
        reason="复用错误",
        idempotency_key="approval-key-001",
    )
    with pytest.raises(DecisionConflictError, match="reused"):
        ledger.approve(
            week_id=WEEK_ID,
            source_type=DecisionType.AI,
            command=reused,
            allowed_codes=set(_names()),
            stock_names=_names(),
            created_at=NOW,
        )


def test_unapproved_decision_cannot_publish() -> None:
    ledger = DecisionLedger()
    ledger.add_source(_source())
    with pytest.raises(DecisionConflictError, match="does not exist"):
        ledger.publish(
            week_id=WEEK_ID,
            decision_version=1,
            idempotency_key="publish-key-001",
        )
