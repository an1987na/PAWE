import uuid
from datetime import UTC, date, datetime

import pytest
from pawe_api.ai.contracts import ErrorAttributionOutput, WeeklyReviewOutput, WeeklySelectionOutput
from pawe_api.ai.credentials import (
    AICredentialCipher,
    AICredentialError,
    key_hint,
    normalize_api_key,
)
from pawe_api.ai.mock_provider import DeterministicMockProvider
from pawe_api.ai.provider import (
    AIProviderConfig,
    AIProviderError,
    AIProviderResult,
    validate_provider_output,
)
from pawe_api.ai.service import AIDomainError, AIService
from pawe_api.config import Settings
from pawe_api.contracts import RuleProposalRequest
from pawe_api.db import models
from pawe_api.db.base import Base
from pawe_api.evaluation.attribution import TAXONOMY, deterministic_attribution_facts
from pawe_api.experiments.rule_dsl import validate_rule_proposal


@pytest.mark.asyncio
async def test_mock_provider_is_deterministic_and_schema_bound() -> None:
    provider = DeterministicMockProvider()
    config = AIProviderConfig("weekly_selection", "mock", False, 1, 128)
    result = await provider.complete(
        config,
        "ignored",
        {"candidates": [{"stock_code": "600000", "evidence_ids": ["e1"]}]},
        WeeklySelectionOutput,
    )
    assert result.provider == "mock"
    assert result.output["analyses"][0]["adjustment"] == 0
    assert WeeklySelectionOutput.model_validate(result.output).analyses[0].stock_code == "600000"


def test_invalid_provider_schema_is_rejected() -> None:
    with pytest.raises(AIProviderError) as error:
        validate_provider_output(
            AIProviderResult("mock", "mock", {"taxonomy": "not-allowed"}, {}),
            ErrorAttributionOutput,
        )
    assert error.value.code == "INVALID_STRUCTURED_OUTPUT"


def test_attribution_has_fixed_taxonomy_and_disables_missing_counterfactual() -> None:
    review = models.WeeklyReview(
        id=uuid.uuid4(),
        week_id=date(2026, 8, 10),
        source_type="legacy",
        source_version=1,
        rule_version="v9.0.0",
        status="degraded",
        entry_trade_date=date(2026, 8, 10),
        final_trade_date=date(2026, 8, 14),
        as_of=datetime(2026, 8, 14, 9, tzinfo=UTC),
        generated_at=datetime(2026, 8, 19, 9, tzinfo=UTC),
        quality="degraded",
        aggregate={"item_count": 0},
        summary="insufficient",
        report_markdown="",
        warnings=["MISSING_DATA"],
        is_active=True,
    )
    facts, taxonomy, allowed, fingerprint = deterministic_attribution_facts(review)
    assert taxonomy == "candidate_coverage_insufficient"
    assert not allowed
    assert facts["counterfactual_warning"] == "FROZEN_CANDIDATE_DATA_INSUFFICIENT"
    assert len(fingerprint) == 64


def test_ai_tables_are_auditable_and_rule_dsl_remains_proposed_only() -> None:
    assert {
        "ai_invocations",
        "ai_audits",
        "ai_candidate_analyses",
        "error_attributions",
        "attribution_resolutions",
    } <= set(Base.metadata.tables)
    request = RuleProposalRequest(
        proposal_id="ai-proposal-001",
        base_rule_version="v9.0.0",
        scope="scoring",
        hypothesis="A bounded scoring adjustment should improve validation retention.",
        conditions={"feature": "return_5d", "op": "gt", "value": 0.0},
        changes=[{"parameter": "price_structure_weight", "value": 1.0}],
        objective=["touch_10_rate"],
        required_features=["return_5d"],
        expected_effect="Only a proposed hypothesis for later validation.",
        invalidation_conditions=["walk-forward fails"],
        rollback_version="v9.0.0",
    )
    assert validate_rule_proposal(request).valid


def test_disabled_service_does_not_select_a_mock_provider() -> None:
    service = AIService(Settings(ai_enabled=False, openai_api_key=None))
    config = AIProviderConfig("weekly_review", "test", False, 1, 128)
    assert service._provider_for(config) is None


def test_mock_provider_requires_explicit_injection() -> None:
    mock = DeterministicMockProvider()
    service = AIService(Settings(ai_enabled=False), mock_provider=mock)
    config = AIProviderConfig("weekly_review", "test", False, 1, 128)
    assert service._provider_for(config) is mock


@pytest.mark.asyncio
async def test_disabled_invocation_is_skipped_without_persisting_mock_output() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.rows: list[object] = []

        def add(self, row: object) -> None:
            self.rows.append(row)

        async def flush(self) -> None:
            return None

        async def commit(self) -> None:
            return None

        async def scalar(self, _statement: object) -> None:
            return None

    session = FakeSession()
    service = AIService(Settings(ai_enabled=False, openai_api_key=None))
    with pytest.raises(AIDomainError, match="AI unavailable"):
        await service._invoke(
            "weekly_review",
            "review",
            "review-1",
            {"items": 0},
            WeeklyReviewOutput,
            uuid.uuid4(),
            session,  # type: ignore[arg-type]
        )
    invocation = session.rows[0]
    assert isinstance(invocation, models.AIInvocation)
    assert invocation.status == "skipped"
    assert invocation.provider == "none"
    assert invocation.structured_output is None
    assert session.rows[1].__class__ is models.AIAudit


def test_personal_ai_key_is_encrypted_and_never_returned_as_hint() -> None:
    settings = Settings(env="test", ai_credential_encryption_key="test-secret-only")
    cipher = AICredentialCipher(settings)
    api_key = "sk-test-personal-key-1234567890"
    encrypted = cipher.encrypt(api_key)
    assert api_key not in encrypted
    assert cipher.decrypt(encrypted) == api_key
    assert key_hint(api_key) == "••••7890"


def test_personal_ai_key_validation_rejects_whitespace() -> None:
    with pytest.raises(AICredentialError, match="format"):
        normalize_api_key("sk-invalid key with whitespace")


def test_attribution_categories_match_formal_strategy_taxonomy() -> None:
    assert {
        "market_state_error",
        "rotation_lag",
        "continuation_overreach",
        "overheat_filter_loose",
        "overheat_filter_strict",
        "stock_selection_error",
        "catalyst_error",
        "confirmation_insufficient",
        "data_anomaly",
        "candidate_coverage_insufficient",
        "anchor_distortion",
        "ai_swap_error",
        "human_override_error",
    } == TAXONOMY
