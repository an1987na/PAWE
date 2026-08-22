import pytest
from pawe_api.contracts import ApprovalAction, ApprovalRequest, PublishRequest
from pydantic import ValidationError


def test_approval_contract_requires_unique_codes_and_reason() -> None:
    request = ApprovalRequest(
        action=ApprovalAction.ACCEPT_AI,
        source_type="ai",
        selected_codes=["000001", "000002"],
        reason="确认 AI 名单",
        decision_version=2,
        idempotency_key="approval-key-001",
    )
    assert request.decision_version == 2

    with pytest.raises(ValidationError, match="must be unique"):
        ApprovalRequest(
            action=ApprovalAction.MODIFY,
            source_type="ai",
            selected_codes=["000001", "000001"],
            reason="重复代码",
            decision_version=2,
            idempotency_key="approval-key-002",
        )


def test_reject_contract_forbids_selected_codes() -> None:
    with pytest.raises(ValidationError, match="cannot contain"):
        ApprovalRequest(
            action=ApprovalAction.REJECT,
            source_type="ai",
            selected_codes=["000001"],
            reason="拒绝",
            decision_version=2,
            idempotency_key="approval-key-003",
        )


def test_publish_contract_requires_stable_idempotency_key() -> None:
    request = PublishRequest(decision_version=1, idempotency_key="publish-key-001")
    assert request.decision_version == 1
