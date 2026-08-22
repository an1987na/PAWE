import uuid
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient
from pawe_api.auth.dependencies import require_admin, require_csrf
from pawe_api.auth.repository import Principal
from pawe_api.contracts import (
    ExperimentApprovalRequest,
    ExperimentResponse,
    ExperimentRunResponse,
    FeatureArtifactResponse,
    RuleProposalRequest,
    RuleProposalResponse,
    SourceCapabilityResponse,
    UserResponse,
)
from pawe_api.main import app, get_governance_application

ADMIN = Principal(
    UserResponse(
        id=str(uuid.uuid4()),
        username="admin",
        role="admin",
        is_active=True,
        created_at=datetime.now(UTC),
    ),
    uuid.uuid4(),
    "csrf",
)


class FakeGovernanceApplication:
    def __init__(self) -> None:
        self.proposal_id = uuid.uuid4()
        self.experiment_id = uuid.uuid4()
        self.now = datetime.now(UTC)

    async def create_proposal(
        self, request: RuleProposalRequest, actor_id: uuid.UUID
    ) -> RuleProposalResponse:
        return RuleProposalResponse(
            id=str(self.proposal_id),
            proposal_id=request.proposal_id,
            version=1,
            status="proposed",
            validation_result={},
            created_at=self.now,
            updated_at=self.now,
        )

    async def validate_proposal(
        self, proposal_id: uuid.UUID, expected_version: int
    ) -> RuleProposalResponse | ExperimentResponse:
        return self._experiment("schema_validated", expected_version)

    async def list_experiments(self) -> list[ExperimentResponse]:
        return [self._experiment("schema_validated", 1)]

    async def queue_run(
        self,
        experiment_id: uuid.UUID,
        run_type: str,
        expected_version: int,
        input_fingerprint: str,
        actor_id: uuid.UUID,
    ) -> ExperimentRunResponse:
        return ExperimentRunResponse(
            id=str(uuid.uuid4()),
            experiment_id=str(experiment_id),
            run_type=run_type,
            attempt=1,
            input_fingerprint=input_fingerprint,
            status="queued",
            metrics={},
            created_at=self.now,
        )

    async def approve(
        self,
        experiment_id: uuid.UUID,
        request: ExperimentApprovalRequest,
        actor_id: uuid.UUID,
    ) -> ExperimentResponse:
        return self._experiment("approved", request.expected_version + 1)

    async def activate(
        self, experiment_id: uuid.UUID, expected_version: int, reason: str
    ) -> ExperimentResponse:
        raise AssertionError("activation must remain disabled by default")

    async def rollback(
        self, experiment_id: uuid.UUID, expected_version: int, reason: str
    ) -> ExperimentResponse:
        return self._experiment("rolled_back", expected_version + 1)

    async def source_capabilities(self) -> list[SourceCapabilityResponse]:
        return [
            SourceCapabilityResponse(
                source_id="tencent",
                adapter_version="1",
                dataset="qfq_daily_bars",
                capabilities={"adjustment": "qfq"},
                market_coverage={"exchanges": ["SSE", "SZSE"]},
                time_semantics={"as_of": True, "fetched_at": True},
                auth_mode="public",
                terms_reviewed_at=date(2026, 8, 13),
                formal_eligibility="formal",
                fallback_priority=1,
                quality="verified",
                last_success_at=self.now,
                last_failure_at=None,
                last_failure_reason=None,
                updated_at=self.now,
            )
        ]

    async def feature_artifacts(self) -> list[FeatureArtifactResponse]:
        return []

    def _experiment(self, status: str, version: int) -> ExperimentResponse:
        return ExperimentResponse(
            id=str(self.experiment_id),
            proposal_id="exp_rule_2026w33_001",
            version=version,
            status=status,
            baseline_rule_version="v9.0.0",
            candidate_rule_version="exp_rule_2026w33_001:v1",
            rollback_version="v9.0.0",
            created_at=self.now,
            updated_at=self.now,
        )


def _proposal_payload() -> dict[str, object]:
    return {
        "proposal_id": "exp_rule_2026w33_001",
        "base_rule_version": "v9.0.0",
        "scope": "scoring",
        "hypothesis": "提高产业链成组强度权重可以减少由单一锚点驱动的候选。",
        "conditions": {"feature": "sector_up_ratio_5d", "op": "gte", "value": 0.7},
        "changes": [{"parameter": "sector_strength_weight", "value": 22}],
        "objective": ["touch_10_rate"],
        "required_features": ["sector_up_ratio_5d"],
        "expected_effect": "提高横向验证充分的非锚点候选排序。",
        "invalidation_conditions": ["单一锚点贡献占比继续上升"],
        "rollback_version": "v9.0.0",
    }


def test_health_exposes_capability_matrix_without_authentication() -> None:
    app.dependency_overrides[get_governance_application] = FakeGovernanceApplication
    try:
        response = TestClient(app).get("/api/v1/health/source-capabilities")
        assert response.status_code == 200
        assert response.json()[0]["formal_eligibility"] == "formal"
    finally:
        app.dependency_overrides.clear()


def test_admin_can_create_and_queue_validated_rule_experiment() -> None:
    fake = FakeGovernanceApplication()
    app.dependency_overrides[get_governance_application] = lambda: fake
    app.dependency_overrides[require_csrf] = lambda: ADMIN
    app.dependency_overrides[require_admin] = lambda: ADMIN
    try:
        created = TestClient(app).post(
            "/api/v1/experiments/rule-proposals", json=_proposal_payload()
        )
        validated = TestClient(app).post(
            f"/api/v1/experiments/rule-proposals/{fake.proposal_id}/validate",
            json={"expected_version": 1},
        )
        queued = TestClient(app).post(
            f"/api/v1/experiments/{fake.experiment_id}/replays",
            json={
                "expected_version": 1,
                "input_fingerprint": "a" * 64,
                "reason": "启动隔离的历史回放验证",
            },
        )
        assert created.status_code == 201
        assert validated.json()["status"] == "schema_validated"
        assert queued.status_code == 201
        assert queued.json()["run_type"] == "replay"
    finally:
        app.dependency_overrides.clear()


def test_experiment_activation_is_disabled_by_default() -> None:
    fake = FakeGovernanceApplication()
    app.dependency_overrides[get_governance_application] = lambda: fake
    app.dependency_overrides[require_csrf] = lambda: ADMIN
    try:
        response = TestClient(app).post(
            f"/api/v1/experiments/{fake.experiment_id}/activate",
            json={"expected_version": 7, "reason": "通过全部门禁后申请激活"},
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "Experiment activation is disabled"
    finally:
        app.dependency_overrides.clear()
