import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pawe_api.ai.audit import fingerprint, safe_context
from pawe_api.contracts import AIAuditResponse, AIInvocationResponse, ErrorAttributionResponse
from pawe_api.db import models


async def save_invocation(
    session: AsyncSession,
    *,
    capability: str,
    subject_type: str,
    subject_id: str,
    provider: str,
    model: str,
    prompt_hash: str,
    schema_version: str,
    policy_version: str,
    input_fingerprint: str,
    structured_input: dict[str, object],
    output: dict[str, object] | None,
    usage: dict[str, object],
    status: str,
    latency_ms: int | None,
    error_code: str | None,
    error_message: str | None,
    actor_id: uuid.UUID | None,
    warnings: list[str],
) -> models.AIInvocation:
    now = datetime.now(UTC)
    invocation = models.AIInvocation(
        id=uuid.uuid4(),
        capability=capability,
        subject_type=subject_type,
        subject_id=subject_id,
        provider=provider,
        model=model,
        prompt_hash=prompt_hash,
        schema_version=schema_version,
        policy_version=policy_version,
        input_fingerprint=input_fingerprint,
        context={
            "capability": capability,
            "subject_id": subject_id,
            **safe_context(structured_input),
        },
        status=status,
        structured_input=structured_input,
        structured_output=output,
        usage=usage,
        latency_ms=latency_ms,
        error_code=error_code,
        error_message=error_message,
        created_by_user_id=actor_id,
        created_at=now,
        finished_at=now,
    )
    session.add(invocation)
    await session.flush()
    session.add(
        models.AIAudit(
            id=uuid.uuid4(),
            invocation_id=invocation.id,
            capability=capability,
            subject_type=subject_type,
            subject_id=subject_id,
            input_fingerprint=input_fingerprint,
            output_hash=fingerprint(output) if output is not None else None,
            validation={"status": status},
            warnings=warnings,
            created_by_user_id=actor_id,
            created_at=now,
        )
    )
    await session.flush()
    return invocation


async def get_invocation(
    session: AsyncSession, invocation_id: uuid.UUID
) -> AIInvocationResponse | None:
    row = await session.get(models.AIInvocation, invocation_id)
    return _invocation_response(row) if row is not None else None


async def list_audits(
    session: AsyncSession, *, capability: str | None = None
) -> list[AIAuditResponse]:
    statement = select(models.AIAudit).order_by(models.AIAudit.created_at.desc()).limit(100)
    if capability is not None:
        statement = statement.where(models.AIAudit.capability == capability)
    rows = list(await session.scalars(statement))
    return [
        AIAuditResponse(
            id=str(row.id),
            invocation_id=str(row.invocation_id),
            capability=row.capability,
            subject_type=row.subject_type,
            subject_id=row.subject_id,
            input_fingerprint=row.input_fingerprint,
            output_hash=row.output_hash,
            validation=row.validation,
            warnings=row.warnings,
            created_at=row.created_at,
        )
        for row in rows
    ]


def attribution_response(row: models.ErrorAttribution) -> ErrorAttributionResponse:
    return ErrorAttributionResponse(
        id=str(row.id),
        week_id=row.week_id,
        review_id=str(row.review_id) if row.review_id else None,
        taxonomy=row.taxonomy,
        confidence=row.confidence,
        facts=row.facts,
        proposed_hypothesis=row.proposed_hypothesis,
        counterfactual_allowed=row.counterfactual_allowed,
        input_fingerprint=row.input_fingerprint,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def list_attributions(session: AsyncSession, week_id: date) -> list[ErrorAttributionResponse]:
    rows = list(
        await session.scalars(
            select(models.ErrorAttribution)
            .where(models.ErrorAttribution.week_id == week_id)
            .order_by(models.ErrorAttribution.created_at.desc())
        )
    )
    return [attribution_response(row) for row in rows]


async def get_attribution(
    session: AsyncSession, attribution_id: uuid.UUID
) -> ErrorAttributionResponse | None:
    row = await session.get(models.ErrorAttribution, attribution_id)
    return attribution_response(row) if row is not None else None


async def resolve_attribution(
    session: AsyncSession, attribution_id: uuid.UUID, action: str, reason: str, actor_id: uuid.UUID
) -> ErrorAttributionResponse | None:
    async with session.begin():
        row = await session.scalar(
            select(models.ErrorAttribution)
            .where(models.ErrorAttribution.id == attribution_id)
            .with_for_update()
        )
        if row is None or row.status != "proposed":
            return None
        row.status = "confirmed" if action == "confirm" else "rejected"
        row.updated_at = datetime.now(UTC)
        session.add(
            models.AttributionResolution(
                id=uuid.uuid4(),
                attribution_id=row.id,
                action=action,
                reason=reason,
                created_by_user_id=actor_id,
                created_at=row.updated_at,
            )
        )
        return attribution_response(row)


def _invocation_response(row: models.AIInvocation) -> AIInvocationResponse:
    return AIInvocationResponse(
        id=str(row.id),
        capability=row.capability,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        provider=row.provider,
        model=row.model,
        prompt_hash=row.prompt_hash,
        schema_version=row.schema_version,
        policy_version=row.policy_version,
        input_fingerprint=row.input_fingerprint,
        status=row.status,
        structured_output=row.structured_output,
        usage=row.usage,
        latency_ms=row.latency_ms,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        finished_at=row.finished_at,
    )
