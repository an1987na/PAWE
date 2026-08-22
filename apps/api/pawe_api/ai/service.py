import time
import uuid
from datetime import UTC, date, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pawe_api.ai.audit import fingerprint
from pawe_api.ai.contracts import (
    ErrorAttributionOutput,
    RuleEvolutionOutput,
    WeeklyReviewOutput,
    WeeklySelectionOutput,
)
from pawe_api.ai.credentials import personal_provider_config
from pawe_api.ai.openai_provider import OpenAIResponsesProvider
from pawe_api.ai.prompts import POLICY_VERSION, PROMPT_SCHEMA_VERSION, prompt_for
from pawe_api.ai.provider import (
    AIProvider,
    AIProviderConfig,
    AIProviderError,
    AIProviderResult,
    validate_provider_output,
)
from pawe_api.ai.repository import _invocation_response, save_invocation
from pawe_api.config import Settings, get_settings
from pawe_api.contracts import (
    AIInvocationResponse,
    AIProposalResponse,
    RuleProposalRequest,
)
from pawe_api.db import models
from pawe_api.evaluation.attribution import deterministic_attribution_facts
from pawe_api.experiments.governance import SqlGovernanceApplication
from pawe_api.experiments.rule_dsl import validate_rule_proposal


class AIDomainError(ValueError):
    pass


class AIService:
    def __init__(
        self, settings: Settings | None = None, *, mock_provider: AIProvider | None = None
    ) -> None:
        self.settings = settings or get_settings()
        # A mock is opt-in for isolated tests; production defaults to no provider.
        self.mock_provider = mock_provider

    async def weekly_selection(
        self,
        session: AsyncSession,
        *,
        week_id: date | None,
        replay_run_id: uuid.UUID | None,
        actor_id: uuid.UUID,
    ) -> AIInvocationResponse:
        subject_type = "replay_run" if replay_run_id is not None else "week"
        subject_id = str(replay_run_id or week_id)
        rows: list[tuple[uuid.UUID, int, str, str, list[str]]] = []
        if replay_run_id is not None:
            result = await session.execute(
                select(models.ReplayDecisionItem, models.Stock)
                .join(models.Stock, models.Stock.id == models.ReplayDecisionItem.stock_id)
                .join(
                    models.ReplayDecisionSet,
                    models.ReplayDecisionSet.id == models.ReplayDecisionItem.replay_decision_set_id,
                )
                .where(models.ReplayDecisionSet.replay_run_id == replay_run_id)
                .order_by(models.ReplayDecisionItem.rank)
            )
            rows = [
                (item.id, stock.id, stock.code, stock.name, [f"replay_decision_item:{item.id}"])
                for item, stock in result.all()
            ]
        elif week_id is not None:
            result = await session.execute(
                select(models.DecisionItem, models.Stock)
                .join(models.Stock, models.Stock.id == models.DecisionItem.stock_id)
                .join(
                    models.DecisionSet, models.DecisionSet.id == models.DecisionItem.decision_set_id
                )
                .where(
                    models.DecisionSet.week_id == week_id,
                    models.DecisionSet.type == "rule",
                    models.DecisionSet.is_active.is_(True),
                )
                .order_by(models.DecisionItem.rank)
            )
            rows = [
                (item.id, stock.id, stock.code, stock.name, [f"decision_item:{item.id}"])
                for item, stock in result.all()
            ]
        if not rows or week_id is None and replay_run_id is None:
            raise AIDomainError("server-side rule/replay candidates are unavailable")
        effective_week_id = week_id
        if effective_week_id is None and replay_run_id is not None:
            replay = await session.get(models.ReplayRun, replay_run_id)
            if replay is None:
                raise AIDomainError("replay run not found")
            effective_week_id = replay.week_id
        payload: dict[str, object] = {
            "week_id": effective_week_id.isoformat() if effective_week_id else None,
            "replay_run_id": str(replay_run_id) if replay_run_id else None,
            "candidates": [
                {"stock_code": code, "stock_name": name, "evidence_ids": evidence}
                for _, _, code, name, evidence in rows
            ],
        }
        invocation, output = await self._invoke(
            "weekly_selection",
            subject_type,
            subject_id,
            payload,
            WeeklySelectionOutput,
            actor_id,
            session,
        )
        analyses = output.analyses
        allowed = {code: set(evidence) for _, _, code, _, evidence in rows}
        if any(item.stock_code not in allowed for item in analyses):
            raise AIDomainError("AI returned an unknown candidate code")
        if any(not set(item.evidence_ids) <= allowed[item.stock_code] for item in analyses):
            raise AIDomainError("AI returned an evidence id outside the candidate whitelist")
        if len(analyses) > 5:
            raise AIDomainError("AI returned more than five analyses")
        if sum(item.adjustment != 0 for item in analyses) > 2:
            raise AIDomainError("AI may replace at most two seats")
        for _, stock_id, code, _, _ in rows:
            analysis = next((item for item in analyses if item.stock_code == code), None)
            if analysis is None:
                continue
            assert effective_week_id is not None
            session.add(
                models.AICandidateAnalysis(
                    id=uuid.uuid4(),
                    invocation_id=invocation.id,
                    replay_run_id=replay_run_id,
                    week_id=effective_week_id,
                    stock_id=stock_id,
                    adjustment=analysis.adjustment,
                    accepted=True,
                    evidence_ids=analysis.evidence_ids,
                    reason=analysis.reason,
                    created_at=datetime.now(UTC),
                )
            )
        await session.flush()
        await session.commit()
        return _invocation_response(invocation)

    async def weekly_review(
        self, session: AsyncSession, *, review_id: uuid.UUID, actor_id: uuid.UUID
    ) -> AIInvocationResponse:
        review = await session.get(models.WeeklyReview, review_id)
        if review is None:
            raise AIDomainError("weekly review not found")
        payload: dict[str, object] = {
            "review_id": str(review.id),
            "week_id": review.week_id.isoformat(),
            "aggregate": review.aggregate,
            "warnings": review.warnings,
            "items": int(cast(int, review.aggregate.get("item_count", 0))),
        }
        invocation, _ = await self._invoke(
            "weekly_review",
            "review",
            str(review.id),
            payload,
            WeeklyReviewOutput,
            actor_id,
            session,
        )
        return _invocation_response(invocation)

    async def error_attribution(
        self, session: AsyncSession, *, review_id: uuid.UUID, actor_id: uuid.UUID
    ) -> models.ErrorAttribution:
        review = await session.get(models.WeeklyReview, review_id)
        if review is None:
            raise AIDomainError("weekly review not found")
        facts, deterministic_taxonomy, counterfactual_allowed, input_fp = (
            deterministic_attribution_facts(review)
        )
        invocation, output = await self._invoke(
            "error_attribution",
            "review",
            str(review.id),
            facts,
            ErrorAttributionOutput,
            actor_id,
            session,
        )
        # Deterministic facts own concrete classifications. AI may suggest a
        # strategy category only when facts remain inconclusive.
        taxonomy = (
            output.taxonomy
            if deterministic_taxonomy == "confirmation_insufficient"
            else deterministic_taxonomy
        )
        row = models.ErrorAttribution(
            id=uuid.uuid4(),
            week_id=review.week_id,
            review_id=review.id,
            invocation_id=invocation.id,
            taxonomy=taxonomy,
            confidence=output.confidence,
            facts=facts,
            proposed_hypothesis=output.hypothesis,
            counterfactual_allowed=counterfactual_allowed and output.counterfactual_allowed,
            input_fingerprint=input_fp,
            status="proposed",
            created_by_user_id=actor_id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(row)
        await session.flush()
        await session.commit()
        return row

    async def rule_evolution(
        self, session: AsyncSession, *, week_id: date, actor_id: uuid.UUID
    ) -> AIProposalResponse:
        attribution = await session.scalar(
            select(models.ErrorAttribution)
            .where(
                models.ErrorAttribution.week_id == week_id,
                models.ErrorAttribution.status == "confirmed",
            )
            .order_by(models.ErrorAttribution.updated_at.desc())
            .limit(1)
        )
        if attribution is None:
            raise AIDomainError("confirmed attribution is required before rule evolution")
        sample_count = attribution.facts.get("item_count")
        if not isinstance(sample_count, int) or sample_count < 3:
            return AIProposalResponse(
                attribution_id=str(attribution.id),
                status="rejected",
                reason="INSUFFICIENT_SAMPLES",
                created_at=datetime.now(UTC),
            )
        payload: dict[str, object] = {
            "week_id": week_id.isoformat(),
            "attribution_id": str(attribution.id),
            "facts": attribution.facts,
            "taxonomy": attribution.taxonomy,
        }
        invocation, output = await self._invoke(
            "rule_evolution",
            "week",
            week_id.isoformat(),
            payload,
            RuleEvolutionOutput,
            actor_id,
            session,
        )
        request = RuleProposalRequest(
            schema_version="1.0",
            proposal_id=output.proposal_id,
            base_rule_version="v9.0.0",
            scope="scoring",
            hypothesis=output.hypothesis,
            conditions={"feature": output.required_features[0], "op": "gt", "value": 0.0},
            changes=[{"parameter": output.parameter, "value": output.value}],
            objective=output.objective,
            required_features=output.required_features,
            expected_effect="仅作为受限 DSL 提案，必须经过时序验证。",
            invalidation_conditions=output.invalidation_conditions,
            rollback_version="v9.0.0",
        )
        validation = validate_rule_proposal(request)
        if not validation.valid:
            return AIProposalResponse(
                attribution_id=str(attribution.id),
                status="rejected",
                reason=";".join(validation.errors),
                created_at=datetime.now(UTC),
            )
        proposal = await SqlGovernanceApplication(session).create_proposal(request, actor_id)
        return AIProposalResponse(
            attribution_id=str(attribution.id),
            proposal_id=proposal.id,
            status="proposed",
            created_at=datetime.now(UTC),
        )

    async def _invoke(
        self,
        capability: str,
        subject_type: str,
        subject_id: str,
        payload: dict[str, object],
        output_model: Any,
        actor_id: uuid.UUID,
        session: AsyncSession,
    ) -> tuple[models.AIInvocation, Any]:
        prompt, prompt_hash = prompt_for(capability)
        personal = await personal_provider_config(session, actor_id, self.settings)
        personal_key = personal[0] if personal else None
        personal_model = personal[1] if personal else None
        config = self._config(
            capability,
            personal_enabled=personal is not None,
            personal_model=personal_model,
        )
        provider = self._provider_for(config, personal_key)
        start = time.monotonic()
        if provider is None:
            invocation = await save_invocation(
                session,
                capability=capability,
                subject_type=subject_type,
                subject_id=subject_id,
                provider="none",
                model=config.model,
                prompt_hash=prompt_hash,
                schema_version=PROMPT_SCHEMA_VERSION,
                policy_version=POLICY_VERSION,
                input_fingerprint=fingerprint(payload),
                structured_input=payload,
                output=None,
                usage={},
                status="skipped",
                latency_ms=int((time.monotonic() - start) * 1000),
                error_code="AI_DISABLED_OR_KEY_MISSING",
                error_message="AI capability is disabled or an API key is unavailable",
                actor_id=actor_id,
                warnings=["AI_UNAVAILABLE_DETERMINISTIC_RESULT_PRESERVED"],
            )
            await session.commit()
            del invocation
            raise AIDomainError(
                "AI unavailable because the capability is disabled or no API key is configured; "
                "the deterministic result was preserved"
            )
        warnings: list[str] = []
        if self.mock_provider is not None and provider is self.mock_provider:
            warnings.append("EXPLICIT_TEST_MOCK_PROVIDER")
        try:
            result: AIProviderResult = await provider.complete(
                config, prompt, payload, output_model
            )
            output = validate_provider_output(result, output_model)
            status = "succeeded" if provider is not self.mock_provider else "mock_succeeded"
            error_code = None
            error_message = None
        except AIProviderError as exc:
            result = AIProviderResult("mock", config.model, {}, {})
            output = None
            status = "failed"
            error_code = exc.code
            error_message = str(exc)
            warnings.append("AI_OUTPUT_NOT_AVAILABLE_PRESERVE_DETERMINISTIC_RESULT")
        invocation = await save_invocation(
            session,
            capability=capability,
            subject_type=subject_type,
            subject_id=subject_id,
            provider=result.provider,
            model=result.model,
            prompt_hash=prompt_hash,
            schema_version=PROMPT_SCHEMA_VERSION,
            policy_version=POLICY_VERSION,
            input_fingerprint=fingerprint(payload),
            structured_input=payload,
            output=output.model_dump(mode="json") if output is not None else None,
            usage=result.usage,
            status=status,
            latency_ms=int((time.monotonic() - start) * 1000),
            error_code=error_code,
            error_message=error_message,
            actor_id=actor_id,
            warnings=warnings,
        )
        await session.commit()
        if output is None:
            raise AIDomainError("AI output unavailable; deterministic result preserved")
        return invocation, output

    def _provider_for(
        self, config: AIProviderConfig, personal_api_key: str | None = None
    ) -> AIProvider | None:
        if config.enabled and personal_api_key:
            return OpenAIResponsesProvider(personal_api_key)
        if config.enabled and self.settings.ai_enabled and self.settings.openai_api_key:
            return OpenAIResponsesProvider(self.settings.openai_api_key)
        return self.mock_provider

    def _config(
        self,
        capability: str,
        *,
        personal_enabled: bool = False,
        personal_model: str | None = None,
    ) -> AIProviderConfig:
        system_enabled = bool(getattr(self.settings, f"ai_{capability}_enabled"))
        enabled = personal_enabled or system_enabled
        model = (
            personal_model
            or getattr(self.settings, f"ai_{capability}_model")
            or self.settings.openai_model
        )
        return AIProviderConfig(
            capability,
            model,
            enabled,
            getattr(self.settings, f"ai_{capability}_timeout_seconds"),
            getattr(self.settings, f"ai_{capability}_max_output_tokens"),
        )
