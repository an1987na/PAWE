import uuid
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient
from pawe_api.auth.dependencies import get_current_principal, require_csrf
from pawe_api.auth.repository import Principal
from pawe_api.contracts import (
    ApprovalAction,
    ApprovalRequest,
    ApprovalResponse,
    Confidence,
    DecisionItem,
    DecisionVersionItem,
    DecisionVersionResponse,
    MarketState,
    PublishRequest,
    UserResponse,
    WeeklyStatus,
    WeekSummary,
)
from pawe_api.decisions.ledger import DecisionConflictError
from pawe_api.main import app, get_decision_application

WEEK_ID = date(2026, 8, 3)
ADMIN = Principal(
    user=UserResponse(
        id=str(uuid.uuid4()),
        username="admin",
        role="admin",
        is_active=True,
        created_at=datetime.now(UTC),
    ),
    session_id=uuid.uuid4(),
    csrf_token_hash="unused-by-override",
)


def _decision(status: str = "approved") -> DecisionVersionResponse:
    return DecisionVersionResponse(
        week_id=WEEK_ID,
        decision_type="published",
        version=1,
        status=status,
        fingerprint="fingerprint",
        source_type="ai",
        source_version=1,
        items=[DecisionVersionItem(stock_code="000001", stock_name="样本1", rank=1)],
    )


class FakeDecisionApplication:
    def __init__(self, *, conflict: bool = False) -> None:
        self.conflict = conflict

    async def current_published(self, today: date) -> WeekSummary | None:
        return WeekSummary(
            week_id=WEEK_ID,
            status=WeeklyStatus.PUBLISHED,
            market_state=MarketState.NORMAL,
            decision_version=1,
            confidence=Confidence.MEDIUM,
            shortage=True,
            shortage_reason="测试仅发布1只",
            items=[
                DecisionItem(
                    stock_code="000001",
                    stock_name="样本1",
                    rank=1,
                    target_return=0.10,
                    confidence=Confidence.MEDIUM,
                    summary="测试摘要",
                    primary_risk="测试风险",
                )
            ],
        )

    async def list_decisions(self, week_id: date) -> list[DecisionVersionResponse]:
        assert week_id == WEEK_ID
        return [_decision()]

    async def approve(self, week_id: date, request: ApprovalRequest) -> ApprovalResponse:
        if self.conflict:
            raise DecisionConflictError("approval decision version is stale")
        return ApprovalResponse(
            approval_id="approval-1",
            action=request.action,
            approved_decision=_decision(),
        )

    async def publish(self, week_id: date, request: PublishRequest) -> DecisionVersionResponse:
        return _decision("published")


def test_decision_approval_and_publish_routes() -> None:
    app.dependency_overrides[get_decision_application] = lambda: FakeDecisionApplication()
    app.dependency_overrides[get_current_principal] = lambda: ADMIN
    app.dependency_overrides[require_csrf] = lambda: ADMIN
    try:
        client = TestClient(app)
        decisions = client.get("/api/v1/weeks/2026-08-03/decisions")
        assert decisions.status_code == 200
        assert decisions.json()[0]["source_type"] == "ai"

        current = client.get("/api/v1/weeks/current")
        assert current.status_code == 200
        assert current.json()["items"][0]["stock_code"] == "000001"

        approval = client.post(
            "/api/v1/weeks/2026-08-03/approval",
            json={
                "action": "accept_ai",
                "source_type": "ai",
                "selected_codes": ["000001"],
                "reason": "确认",
                "decision_version": 1,
                "idempotency_key": "approval-key-001",
            },
        )
        assert approval.status_code == 200
        assert approval.json()["approved_decision"]["status"] == "approved"

        published = client.post(
            "/api/v1/weeks/2026-08-03/publish",
            json={"decision_version": 1, "idempotency_key": "publish-key-001"},
        )
        assert published.status_code == 200
        assert published.json()["status"] == "published"
    finally:
        app.dependency_overrides.clear()


def test_stale_approval_maps_to_http_conflict() -> None:
    app.dependency_overrides[get_decision_application] = lambda: FakeDecisionApplication(
        conflict=True
    )
    app.dependency_overrides[require_csrf] = lambda: ADMIN
    try:
        response = TestClient(app).post(
            "/api/v1/weeks/2026-08-03/approval",
            json={
                "action": ApprovalAction.ACCEPT_AI.value,
                "source_type": "ai",
                "selected_codes": ["000001"],
                "reason": "过期版本",
                "decision_version": 1,
                "idempotency_key": "approval-key-002",
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "approval decision version is stale"
    finally:
        app.dependency_overrides.clear()
