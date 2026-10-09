import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from pawe_api import main as api
from pawe_api.ai.mock_provider import DeterministicMockProvider
from pawe_api.ai.provider import AIProviderError, AIProviderResult
from pawe_api.ai.service import AIService
from pawe_api.auth.dependencies import get_current_principal, require_csrf
from pawe_api.auth.repository import Principal
from pawe_api.config import Settings
from pawe_api.contracts import UserResponse
from pawe_api.db import models
from pawe_api.db.session import get_db_session
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool


@pytest.mark.asyncio
async def test_ai_tasks_preserve_decisions_and_audit_failures_in_real_postgres() -> None:
    url = os.environ.get("PAWE_DATABASE_URL", "")
    if "@127.0.0.1:55432/pawe_native_test" not in url:
        pytest.skip("Requires the explicit isolated native test database")
    engine = create_async_engine(url, poolclass=NullPool)
    now = datetime.now(UTC)
    week = date(2026, 9, 28)
    actor = uuid.uuid4()
    async with engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as session:
            session.add(
                models.User(
                    id=actor,
                    username=f"test-{actor.hex[:8]}",
                    password_hash="unused-test-hash",
                    role="admin",
                    is_active=True,
                    created_at=now,
                    password_changed_at=now,
                )
            )
            session.add(
                models.Week(
                    week_id=week, status="rule_ready", market_state="NORMAL", rule_version="v9.0.0"
                )
            )
            await session.flush()
            decision = models.DecisionSet(
                id=uuid.uuid4(),
                week_id=week,
                type="rule",
                version=1,
                status="generated",
                fingerprint="a" * 64,
                shortage=True,
                is_active=True,
                created_at=now,
            )
            review = models.WeeklyReview(
                id=uuid.uuid4(),
                week_id=week,
                source_type="published",
                source_version=1,
                rule_version="v9.0.0",
                status="published",
                entry_trade_date=week,
                final_trade_date=date(2026, 9, 30),
                as_of=now,
                generated_at=now,
                quality="single_source",
                aggregate={"item_count": 3},
                summary="本地虚构复盘，仅用于隔离测试",
                report_markdown="本地虚构",
                warnings=["LOCAL_TEST_FIXTURE"],
                is_active=True,
            )
            session.add_all([decision, review])
            await session.flush()
            for rank, code in enumerate(["600000", "600001", "600002"], 1):
                stock = models.Stock(
                    code=code,
                    name="本地虚构标的",
                    exchange="SSE",
                    board="main",
                    listing_date=date(2000, 1, 1),
                    status="active",
                )
                session.add(stock)
                await session.flush()
                session.add(
                    models.DecisionItem(
                        id=uuid.uuid4(),
                        decision_set_id=decision.id,
                        stock_id=stock.id,
                        rank=rank,
                        role="primary",
                        target_return=Decimal("0.1"),
                        confidence="low",
                        summary="本地测试候选",
                        primary_risk="虚构数据",
                    )
                )
            await session.flush()
            principal = Principal(
                UserResponse(
                    id=str(actor),
                    username="test-admin",
                    role="admin",
                    is_active=True,
                    created_at=now,
                ),
                uuid.uuid4(),
                "csrf-test",
            )
            settings = Settings(_env_file=None, ai_enabled=False, openai_api_key=None)
            service = AIService(settings, mock_provider=DeterministicMockProvider())
            api.app.dependency_overrides[get_db_session] = lambda: session
            api.app.dependency_overrides[get_current_principal] = lambda: principal
            api.app.dependency_overrides[require_csrf] = lambda: principal
            api.app.dependency_overrides[api.get_ai_service] = lambda: service
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=api.app), base_url="http://test"
                ) as client:
                    for capability in ["weekly_selection", "weekly_review"]:
                        response = await client.post(
                            "/api/v1/ai/tasks",
                            json={"capability": capability, "week_id": week.isoformat()},
                        )
                        assert response.status_code == 201, response.text
                        assert response.json()["status"] == "mock_succeeded"
                    # Both workbench (week) and history (exact review) resolve correctly.
                    for target in [{"week_id": week.isoformat()}, {"review_id": str(review.id)}]:
                        response = await client.post(
                            "/api/v1/ai/tasks", json={"capability": "error_attribution", **target}
                        )
                        assert response.status_code == 201, response.text
                        assert response.json()["status"] == "proposed"
                        assert response.json()["review_id"] == str(review.id)
                    rejected = await client.post(
                        "/api/v1/ai/tasks",
                        json={"capability": "rule_evolution", "week_id": week.isoformat()},
                    )
                    assert rejected.status_code == 422  # No automatic confirmation.
                    attribution = await session.scalar(select(models.ErrorAttribution).limit(1))
                    assert attribution is not None
                    attribution.status = "confirmed"  # Only this rolled-back test fixture.
                    await session.commit()
                    proposed = await client.post(
                        "/api/v1/ai/tasks",
                        json={"capability": "rule_evolution", "week_id": week.isoformat()},
                    )
                    assert proposed.status_code == 201, proposed.text
                    assert proposed.json()["status"] == "proposed"
                    assert decision.fingerprint == "a" * 64 and decision.status == "generated"

                    class InvalidSelectionProvider:
                        def __init__(self, analyses: list[dict[str, object]]) -> None:
                            self.analyses = analyses

                        async def complete(self, *_args: object) -> AIProviderResult:
                            return AIProviderResult(
                                "openai_responses",
                                "gpt-6.1-sol",
                                {"analyses": self.analyses},
                                {"total_tokens": 123},
                            )

                    service.mock_provider = None
                    for error_code, analyses in [
                        (
                            "AI_UNKNOWN_CANDIDATE",
                            [
                                {
                                    "stock_code": "600009",
                                    "adjustment": 0,
                                    "evidence_ids": [],
                                    "reason": "本地虚构候选白名单测试",
                                }
                            ],
                        ),
                        (
                            "AI_UNKNOWN_EVIDENCE",
                            [
                                {
                                    "stock_code": "600000",
                                    "adjustment": 0,
                                    "evidence_ids": ["invented"],
                                    "reason": "本地虚构证据白名单测试",
                                }
                            ],
                        ),
                        (
                            "AI_ADJUSTMENT_LIMIT",
                            [
                                {
                                    "stock_code": code,
                                    "adjustment": 1,
                                    "evidence_ids": [],
                                    "reason": "本地虚构调整容量边界测试",
                                }
                                for code in ["600000", "600001", "600002"]
                            ],
                        ),
                    ]:
                        provider = InvalidSelectionProvider(analyses)
                        service._provider_for = lambda *_args, provider=provider: provider  # type: ignore[method-assign,assignment,return-value]
                        invalid = await client.post(
                            "/api/v1/ai/tasks",
                            json={"capability": "weekly_selection", "week_id": week.isoformat()},
                        )
                        assert invalid.status_code == 422 and error_code in invalid.text
                        invalid_invocation = await session.scalar(
                            select(models.AIInvocation).where(
                                models.AIInvocation.error_code == error_code
                            )
                        )
                        assert invalid_invocation is not None
                        assert invalid_invocation.status == "failed"
                        assert invalid_invocation.structured_output is None
                        assert invalid_invocation.usage == {"total_tokens": 123}
                        assert decision.fingerprint == "a" * 64

                    class FailedProvider:
                        async def complete(self, *_args: object) -> object:
                            raise AIProviderError("OPENAI_AUTH_FAILED", "OpenAI API Key 无效。")

                    service.mock_provider = None
                    service._provider_for = lambda *_args: FailedProvider()  # type: ignore[method-assign,assignment,return-value]
                    failed = await client.post(
                        "/api/v1/ai/tasks",
                        json={"capability": "error_attribution", "review_id": str(review.id)},
                    )
                    assert failed.status_code == 422
                    assert "OPENAI_AUTH_FAILED" in failed.text
                    invocation = await session.scalar(
                        select(models.AIInvocation).where(
                            models.AIInvocation.error_code == "OPENAI_AUTH_FAILED"
                        )
                    )
                    assert invocation is not None
                    assert invocation.provider == "openai_responses"
                    assert invocation.structured_output is None
                    audit = await session.scalar(
                        select(models.AIAudit).where(models.AIAudit.invocation_id == invocation.id)
                    )
                    assert (
                        audit is not None and audit.validation["error_code"] == "OPENAI_AUTH_FAILED"
                    )
            finally:
                api.app.dependency_overrides.clear()
        await transaction.rollback()
    await engine.dispose()
