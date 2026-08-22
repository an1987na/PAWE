import uuid
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pawe_api.contracts import (
    ExperimentApprovalRequest,
    ExperimentFoldResult,
    ExperimentResponse,
    ExperimentRunCompleteRequest,
    ExperimentRunResponse,
    ExperimentRunUpdateResponse,
    FeatureArtifactResponse,
    RuleProposalRequest,
    RuleProposalResponse,
    SourceCapabilityResponse,
)
from pawe_api.db import models
from pawe_api.experiments.rule_dsl import validate_rule_proposal
from pawe_api.experiments.state import ExperimentStateError, require_transition


class GovernanceNotFoundError(LookupError):
    pass


class GovernanceConflictError(ValueError):
    pass


class GovernanceApplication(Protocol):
    async def create_proposal(
        self, request: RuleProposalRequest, actor_id: uuid.UUID
    ) -> RuleProposalResponse: ...

    async def validate_proposal(
        self, proposal_id: uuid.UUID, expected_version: int
    ) -> RuleProposalResponse | ExperimentResponse: ...

    async def list_experiments(self) -> list[ExperimentResponse]: ...

    async def queue_run(
        self,
        experiment_id: uuid.UUID,
        run_type: str,
        expected_version: int,
        input_fingerprint: str,
        actor_id: uuid.UUID,
    ) -> ExperimentRunResponse: ...

    async def approve(
        self,
        experiment_id: uuid.UUID,
        request: ExperimentApprovalRequest,
        actor_id: uuid.UUID,
    ) -> ExperimentResponse: ...

    async def start_run(
        self, run_id: uuid.UUID, expected_version: int
    ) -> ExperimentRunUpdateResponse: ...

    async def complete_run(
        self, run_id: uuid.UUID, request: ExperimentRunCompleteRequest
    ) -> ExperimentRunUpdateResponse: ...

    async def prepare_shadow(
        self, experiment_id: uuid.UUID, expected_version: int, reason: str
    ) -> ExperimentResponse: ...

    async def activate(
        self, experiment_id: uuid.UUID, expected_version: int, reason: str
    ) -> ExperimentResponse: ...

    async def rollback(
        self, experiment_id: uuid.UUID, expected_version: int, reason: str
    ) -> ExperimentResponse: ...

    async def source_capabilities(self) -> list[SourceCapabilityResponse]: ...

    async def feature_artifacts(self) -> list[FeatureArtifactResponse]: ...


class SqlGovernanceApplication:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_proposal(
        self, request: RuleProposalRequest, actor_id: uuid.UUID
    ) -> RuleProposalResponse:
        now = datetime.now(UTC)
        async with self.session.begin():
            existing = await self.session.scalar(
                select(models.RuleProposal).where(
                    models.RuleProposal.proposal_key == request.proposal_id
                )
            )
            if existing is not None:
                raise GovernanceConflictError("proposal_id already exists")
            row = models.RuleProposal(
                id=uuid.uuid4(),
                proposal_key=request.proposal_id,
                version=1,
                schema_version=request.schema_version,
                base_rule_version=request.base_rule_version,
                scope=request.scope,
                hypothesis=request.hypothesis,
                dsl=request.model_dump(mode="json", by_alias=True),
                objectives=list(request.objective),
                required_features=request.required_features,
                invalidation_conditions=request.invalidation_conditions,
                rollback_version=request.rollback_version,
                status="proposed",
                validation_result={},
                created_by_user_id=actor_id,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
            await self.session.flush()
            return _proposal_response(row)

    async def validate_proposal(
        self, proposal_id: uuid.UUID, expected_version: int
    ) -> RuleProposalResponse | ExperimentResponse:
        now = datetime.now(UTC)
        async with self.session.begin():
            proposal = await self.session.scalar(
                select(models.RuleProposal)
                .where(models.RuleProposal.id == proposal_id)
                .with_for_update()
            )
            if proposal is None:
                raise GovernanceNotFoundError("rule proposal not found")
            if proposal.version != expected_version or proposal.status != "proposed":
                raise GovernanceConflictError("rule proposal version or state has changed")
            request = RuleProposalRequest.model_validate(proposal.dsl)
            result = validate_rule_proposal(request)
            proposal.version += 1
            proposal.validation_result = result.payload()
            proposal.status = "schema_validated" if result.valid else "invalid"
            proposal.updated_at = now
            if not result.valid:
                await self.session.flush()
                return _proposal_response(proposal)
            experiment = models.Experiment(
                id=uuid.uuid4(),
                rule_proposal_id=proposal.id,
                version=1,
                status="schema_validated",
                baseline_rule_version=proposal.base_rule_version,
                candidate_rule_version=f"{proposal.proposal_key}:v1",
                rollback_version=proposal.rollback_version,
                activated_rule_version=None,
                status_reason="Static DSL validation passed",
                created_at=now,
                updated_at=now,
            )
            self.session.add(experiment)
            await self.session.flush()
            return _experiment_response(experiment, proposal.proposal_key)

    async def list_experiments(self) -> list[ExperimentResponse]:
        rows = await self.session.execute(
            select(models.Experiment, models.RuleProposal.proposal_key)
            .join(
                models.RuleProposal,
                models.RuleProposal.id == models.Experiment.rule_proposal_id,
            )
            .order_by(models.Experiment.created_at.desc())
        )
        return [_experiment_response(experiment, key) for experiment, key in rows]

    async def queue_run(
        self,
        experiment_id: uuid.UUID,
        run_type: str,
        expected_version: int,
        input_fingerprint: str,
        actor_id: uuid.UUID,
    ) -> ExperimentRunResponse:
        now = datetime.now(UTC)
        expected_status = "schema_validated" if run_type == "replay" else "shadow_ready"
        target_status = "replay_queued" if run_type == "replay" else "shadow_running"
        async with self.session.begin():
            experiment = await self._locked_experiment(experiment_id)
            self._check_version_and_state(experiment, expected_version, expected_status)
            require_transition(experiment.status, target_status)
            attempt = await self.session.scalar(
                select(func.count(models.ExperimentRun.id)).where(
                    models.ExperimentRun.experiment_id == experiment.id,
                    models.ExperimentRun.run_type == run_type,
                )
            )
            run = models.ExperimentRun(
                id=uuid.uuid4(),
                experiment_id=experiment.id,
                run_type=run_type,
                attempt=int(attempt or 0) + 1,
                input_fingerprint=input_fingerprint,
                status="queued" if run_type == "replay" else "running",
                metrics={},
                failure_reason=None,
                created_by_user_id=actor_id,
                created_at=now,
                started_at=now if run_type == "shadow" else None,
                finished_at=None,
            )
            experiment.status = target_status
            experiment.version += 1
            experiment.status_reason = f"{run_type} run {run.attempt} created"
            experiment.updated_at = now
            self.session.add(run)
            await self.session.flush()
            return _run_response(run)

    async def approve(
        self,
        experiment_id: uuid.UUID,
        request: ExperimentApprovalRequest,
        actor_id: uuid.UUID,
    ) -> ExperimentResponse:
        now = datetime.now(UTC)
        target = "approved" if request.action == "approve" else "replay_rejected"
        async with self.session.begin():
            experiment = await self._locked_experiment(experiment_id)
            self._check_version_and_state(experiment, request.expected_version, "awaiting_approval")
            require_transition(experiment.status, target)
            approval = models.ExperimentApproval(
                id=uuid.uuid4(),
                experiment_id=experiment.id,
                experiment_version=experiment.version,
                action=request.action,
                reason=request.reason,
                created_by_user_id=actor_id,
                created_at=now,
            )
            experiment.status = target
            experiment.version += 1
            experiment.status_reason = request.reason
            experiment.updated_at = now
            self.session.add(approval)
            proposal_key = await self._proposal_key(experiment.rule_proposal_id)
            return _experiment_response(experiment, proposal_key)

    async def start_run(
        self, run_id: uuid.UUID, expected_version: int
    ) -> ExperimentRunUpdateResponse:
        now = datetime.now(UTC)
        async with self.session.begin():
            run = await self._locked_run(run_id)
            if run.run_type != "replay" or run.status != "queued":
                raise GovernanceConflictError("only a queued replay run can be started")
            experiment = await self._locked_experiment(run.experiment_id)
            self._check_version_and_state(experiment, expected_version, "replay_queued")
            require_transition(experiment.status, "replay_running")
            run.status = "running"
            run.started_at = now
            experiment.status = "replay_running"
            experiment.version += 1
            experiment.status_reason = f"replay run {run.attempt} started"
            experiment.updated_at = now
            proposal_key = await self._proposal_key(experiment.rule_proposal_id)
            return ExperimentRunUpdateResponse(
                run=_run_response(run),
                experiment=_experiment_response(experiment, proposal_key),
            )

    async def complete_run(
        self, run_id: uuid.UUID, request: ExperimentRunCompleteRequest
    ) -> ExperimentRunUpdateResponse:
        now = datetime.now(UTC)
        async with self.session.begin():
            run = await self._locked_run(run_id)
            if run.status != "running":
                raise GovernanceConflictError("only a running experiment can be completed")
            experiment = await self._locked_experiment(run.experiment_id)
            expected_status = "replay_running" if run.run_type == "replay" else "shadow_running"
            self._check_version_and_state(experiment, request.expected_version, expected_status)
            target = _completion_target(run.run_type, request.outcome)
            require_transition(experiment.status, target)
            if run.run_type == "shadow" and request.folds:
                raise GovernanceConflictError("shadow runs cannot write walk-forward folds")
            if run.run_type == "replay":
                self._add_folds(run.id, request.folds)
            run.status = {
                "passed": "succeeded",
                "rejected": "rejected",
                "failed": "failed",
            }[request.outcome]
            run.metrics = request.metrics
            run.failure_reason = request.failure_reason
            run.finished_at = now
            experiment.status = target
            experiment.version += 1
            experiment.status_reason = request.failure_reason or f"{run.run_type} {request.outcome}"
            experiment.updated_at = now
            proposal_key = await self._proposal_key(experiment.rule_proposal_id)
            return ExperimentRunUpdateResponse(
                run=_run_response(run),
                experiment=_experiment_response(experiment, proposal_key),
            )

    async def prepare_shadow(
        self, experiment_id: uuid.UUID, expected_version: int, reason: str
    ) -> ExperimentResponse:
        return await self._final_transition(
            experiment_id,
            expected_version,
            "replay_passed",
            "shadow_ready",
            reason,
        )

    async def activate(
        self, experiment_id: uuid.UUID, expected_version: int, reason: str
    ) -> ExperimentResponse:
        return await self._final_transition(
            experiment_id, expected_version, "approved", "activated", reason
        )

    async def rollback(
        self, experiment_id: uuid.UUID, expected_version: int, reason: str
    ) -> ExperimentResponse:
        return await self._final_transition(
            experiment_id, expected_version, "activated", "rolled_back", reason
        )

    async def source_capabilities(self) -> list[SourceCapabilityResponse]:
        rows = await self.session.scalars(
            select(models.SourceCapability).order_by(
                models.SourceCapability.dataset,
                models.SourceCapability.fallback_priority,
                models.SourceCapability.source_id,
            )
        )
        return [_source_response(row) for row in rows]

    async def feature_artifacts(self) -> list[FeatureArtifactResponse]:
        rows = await self.session.scalars(
            select(models.FeatureArtifact)
            .order_by(models.FeatureArtifact.created_at.desc())
            .limit(200)
        )
        return [_artifact_response(row) for row in rows]

    async def _final_transition(
        self,
        experiment_id: uuid.UUID,
        expected_version: int,
        expected_status: str,
        target_status: str,
        reason: str,
    ) -> ExperimentResponse:
        now = datetime.now(UTC)
        async with self.session.begin():
            experiment = await self._locked_experiment(experiment_id)
            self._check_version_and_state(experiment, expected_version, expected_status)
            require_transition(experiment.status, target_status)
            experiment.status = target_status
            experiment.version += 1
            experiment.status_reason = reason
            experiment.updated_at = now
            experiment.activated_rule_version = (
                experiment.candidate_rule_version if target_status == "activated" else None
            )
            proposal_key = await self._proposal_key(experiment.rule_proposal_id)
            return _experiment_response(experiment, proposal_key)

    async def _locked_experiment(self, experiment_id: uuid.UUID) -> models.Experiment:
        experiment = await self.session.scalar(
            select(models.Experiment)
            .where(models.Experiment.id == experiment_id)
            .with_for_update()
        )
        if experiment is None:
            raise GovernanceNotFoundError("experiment not found")
        return experiment

    async def _locked_run(self, run_id: uuid.UUID) -> models.ExperimentRun:
        run = await self.session.scalar(
            select(models.ExperimentRun)
            .where(models.ExperimentRun.id == run_id)
            .with_for_update()
        )
        if run is None:
            raise GovernanceNotFoundError("experiment run not found")
        return run

    def _add_folds(
        self, run_id: uuid.UUID, folds: list[ExperimentFoldResult]
    ) -> None:
        for fold in folds:
            self.session.add(
                models.ExperimentFold(
                    id=uuid.uuid4(),
                    run_id=run_id,
                    fold_index=fold.fold_index,
                    train_start=fold.train_start,
                    train_end=fold.train_end,
                    selection_start=fold.selection_start,
                    selection_end=fold.selection_end,
                    validation_start=fold.validation_start,
                    validation_end=fold.validation_end,
                    snapshot_ids=fold.snapshot_ids,
                    sample_count=fold.sample_count,
                    capacity_distribution=fold.capacity_distribution,
                    metrics=fold.metrics,
                    integrity_status=fold.integrity_status,
                )
            )

    @staticmethod
    def _check_version_and_state(
        experiment: models.Experiment, expected_version: int, expected_status: str
    ) -> None:
        if experiment.version != expected_version or experiment.status != expected_status:
            raise GovernanceConflictError("experiment version or state has changed")

    async def _proposal_key(self, proposal_id: uuid.UUID) -> str:
        key = await self.session.scalar(
            select(models.RuleProposal.proposal_key).where(models.RuleProposal.id == proposal_id)
        )
        if key is None:
            raise GovernanceNotFoundError("rule proposal not found")
        return key


def _proposal_response(row: models.RuleProposal) -> RuleProposalResponse:
    return RuleProposalResponse(
        id=str(row.id),
        proposal_id=row.proposal_key,
        version=row.version,
        status=row.status,
        validation_result=row.validation_result,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _experiment_response(row: models.Experiment, proposal_key: str) -> ExperimentResponse:
    return ExperimentResponse(
        id=str(row.id),
        proposal_id=proposal_key,
        version=row.version,
        status=row.status,
        baseline_rule_version=row.baseline_rule_version,
        candidate_rule_version=row.candidate_rule_version,
        rollback_version=row.rollback_version,
        activated_rule_version=row.activated_rule_version,
        status_reason=row.status_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _run_response(row: models.ExperimentRun) -> ExperimentRunResponse:
    return ExperimentRunResponse(
        id=str(row.id),
        experiment_id=str(row.experiment_id),
        run_type=row.run_type,
        attempt=row.attempt,
        input_fingerprint=row.input_fingerprint,
        status=row.status,
        metrics=row.metrics,
        failure_reason=row.failure_reason,
        created_at=row.created_at,
    )


def _source_response(row: models.SourceCapability) -> SourceCapabilityResponse:
    return SourceCapabilityResponse(
        source_id=row.source_id,
        adapter_version=row.adapter_version,
        dataset=row.dataset,
        capabilities=row.capabilities,
        market_coverage=row.market_coverage,
        time_semantics=row.time_semantics,
        auth_mode=row.auth_mode,
        terms_reviewed_at=row.terms_reviewed_at,
        formal_eligibility=row.formal_eligibility,
        fallback_priority=row.fallback_priority,
        quality=row.quality,
        last_success_at=row.last_success_at,
        last_failure_at=row.last_failure_at,
        last_failure_reason=row.last_failure_reason,
        updated_at=row.updated_at,
    )


def _artifact_response(row: models.FeatureArtifact) -> FeatureArtifactResponse:
    return FeatureArtifactResponse(
        id=str(row.id),
        snapshot_id=str(row.snapshot_id),
        partition_key=row.partition_key,
        schema_version=row.schema_version,
        feature_version=row.feature_version,
        code_version=row.code_version,
        decision_cutoff=row.decision_cutoff,
        row_count=row.row_count,
        content_hash=row.content_hash,
        quality=row.quality,
        status=row.status,
        uri=row.uri,
        created_at=row.created_at,
        published_at=row.published_at,
    )


def map_governance_error(exc: Exception) -> tuple[int, str]:
    if isinstance(exc, GovernanceNotFoundError):
        return 404, str(exc)
    if isinstance(exc, (GovernanceConflictError, ExperimentStateError)):
        return 409, str(exc)
    return 422, "experiment request is invalid"


def _completion_target(run_type: str, outcome: str) -> str:
    if run_type == "replay":
        return {
            "passed": "replay_passed",
            "rejected": "replay_rejected",
            "failed": "replay_failed",
        }[outcome]
    return "awaiting_approval" if outcome == "passed" else "shadow_failed"
