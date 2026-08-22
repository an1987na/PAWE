import json
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum


class DecisionConflictError(ValueError):
    pass


class DecisionValidationError(ValueError):
    pass


class DecisionType(StrEnum):
    RULE = "rule"
    AI = "ai"
    PUBLISHED = "published"


class DecisionStatus(StrEnum):
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"


class ApprovalAction(StrEnum):
    ACCEPT_RULE = "accept_rule"
    ACCEPT_AI = "accept_ai"
    MODIFY = "modify"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class DecisionItemRecord:
    stock_code: str
    stock_name: str
    rank: int


@dataclass(frozen=True, slots=True)
class DecisionSetRecord:
    id: uuid.UUID
    week_id: date
    decision_type: DecisionType
    version: int
    status: DecisionStatus
    fingerprint: str
    items: tuple[DecisionItemRecord, ...]
    source_type: DecisionType | None
    source_version: int | None
    is_active: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ApprovalCommand:
    action: ApprovalAction
    decision_version: int
    selected_codes: tuple[str, ...]
    reason: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    id: uuid.UUID
    week_id: date
    source_type: DecisionType
    source_version: int
    action: ApprovalAction
    selected_codes: tuple[str, ...]
    reason: str
    idempotency_key: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ApprovalOutcome:
    approval: ApprovalRecord
    approved_decision: DecisionSetRecord | None


class DecisionLedger:
    def __init__(self) -> None:
        self._sets: list[DecisionSetRecord] = []
        self._approval_keys: dict[tuple[date, str], tuple[str, ApprovalOutcome]] = {}
        self._publish_keys: dict[tuple[date, str], tuple[int, DecisionSetRecord]] = {}

    def add_source(self, decision: DecisionSetRecord) -> None:
        if decision.decision_type not in {DecisionType.RULE, DecisionType.AI}:
            raise DecisionValidationError("only rule or AI decisions can be approval sources")
        if decision.status is not DecisionStatus.AWAITING_APPROVAL:
            raise DecisionValidationError("approval source must be awaiting approval")
        if self._find(decision.week_id, decision.decision_type, decision.version) is not None:
            raise DecisionConflictError("decision version already exists")
        if any(
            existing.week_id == decision.week_id
            and existing.decision_type is decision.decision_type
            and existing.is_active
            for existing in self._sets
        ):
            raise DecisionConflictError("an active decision source already exists")
        self._validate_items(decision.items)
        self._sets.append(decision)

    def approve(
        self,
        *,
        week_id: date,
        source_type: DecisionType,
        command: ApprovalCommand,
        allowed_codes: set[str],
        stock_names: dict[str, str],
        created_at: datetime,
    ) -> ApprovalOutcome:
        payload = _approval_payload(source_type, command)
        key = (week_id, command.idempotency_key)
        existing = self._approval_keys.get(key)
        if existing is not None:
            if existing[0] != payload:
                raise DecisionConflictError("approval idempotency key was reused")
            return existing[1]
        source = self._active_source(week_id, source_type)
        if source.version != command.decision_version:
            raise DecisionConflictError("approval decision version is stale")
        if source.status is not DecisionStatus.AWAITING_APPROVAL:
            raise DecisionValidationError("decision is not awaiting approval")
        selected_codes = _validated_selection(source, source_type, command, allowed_codes)
        approval = ApprovalRecord(
            id=uuid.uuid4(),
            week_id=week_id,
            source_type=source_type,
            source_version=source.version,
            action=command.action,
            selected_codes=selected_codes,
            reason=command.reason,
            idempotency_key=command.idempotency_key,
            created_at=created_at,
        )
        approved = None
        if command.action is not ApprovalAction.REJECT:
            approved = self._create_approved_decision(
                source=source,
                selected_codes=selected_codes,
                stock_names=stock_names,
                created_at=created_at,
            )
            self._replace(source, replace(source, status=DecisionStatus.APPROVED))
        outcome = ApprovalOutcome(approval, approved)
        self._approval_keys[key] = (payload, outcome)
        return outcome

    def publish(
        self,
        *,
        week_id: date,
        decision_version: int,
        idempotency_key: str,
    ) -> DecisionSetRecord:
        key = (week_id, idempotency_key)
        existing = self._publish_keys.get(key)
        if existing is not None:
            if existing[0] != decision_version:
                raise DecisionConflictError("publish idempotency key was reused")
            return existing[1]
        decision = self._find(week_id, DecisionType.PUBLISHED, decision_version)
        if decision is None:
            raise DecisionConflictError("published decision version does not exist")
        if decision.status is not DecisionStatus.APPROVED:
            raise DecisionValidationError("only an approved decision can be published")
        published = replace(decision, status=DecisionStatus.PUBLISHED)
        self._replace(decision, published)
        self._publish_keys[key] = (decision_version, published)
        return published

    def decisions_for_week(self, week_id: date) -> tuple[DecisionSetRecord, ...]:
        return tuple(decision for decision in self._sets if decision.week_id == week_id)

    def _active_source(self, week_id: date, source_type: DecisionType) -> DecisionSetRecord:
        if source_type not in {DecisionType.RULE, DecisionType.AI}:
            raise DecisionValidationError("approval source must be rule or AI")
        active = [
            decision
            for decision in self._sets
            if decision.week_id == week_id
            and decision.decision_type is source_type
            and decision.is_active
        ]
        if len(active) != 1:
            raise DecisionConflictError("exactly one active approval source is required")
        return active[0]

    def _create_approved_decision(
        self,
        *,
        source: DecisionSetRecord,
        selected_codes: tuple[str, ...],
        stock_names: dict[str, str],
        created_at: datetime,
    ) -> DecisionSetRecord:
        existing_active = [
            decision
            for decision in self._sets
            if decision.week_id == source.week_id
            and decision.decision_type is DecisionType.PUBLISHED
            and decision.is_active
        ]
        for decision in existing_active:
            self._replace(
                decision,
                replace(
                    decision,
                    status=(
                        DecisionStatus.PUBLISHED
                        if decision.status is DecisionStatus.PUBLISHED
                        else DecisionStatus.SUPERSEDED
                    ),
                    is_active=False,
                ),
            )
        versions = [
            decision.version
            for decision in self._sets
            if decision.week_id == source.week_id
            and decision.decision_type is DecisionType.PUBLISHED
        ]
        version = max(versions, default=0) + 1
        if not set(selected_codes) <= stock_names.keys():
            raise DecisionValidationError("stock name is missing for a selected code")
        items = tuple(
            DecisionItemRecord(code, stock_names[code], rank)
            for rank, code in enumerate(selected_codes, start=1)
        )
        fingerprint = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"pawe:{source.week_id}:{version}:{','.join(selected_codes)}",
        ).hex
        approved = DecisionSetRecord(
            id=uuid.uuid4(),
            week_id=source.week_id,
            decision_type=DecisionType.PUBLISHED,
            version=version,
            status=DecisionStatus.APPROVED,
            fingerprint=fingerprint,
            items=items,
            source_type=source.decision_type,
            source_version=source.version,
            is_active=True,
            created_at=created_at,
        )
        self._sets.append(approved)
        return approved

    def _find(
        self, week_id: date, decision_type: DecisionType, version: int
    ) -> DecisionSetRecord | None:
        return next(
            (
                decision
                for decision in self._sets
                if decision.week_id == week_id
                and decision.decision_type is decision_type
                and decision.version == version
            ),
            None,
        )

    def _replace(self, old: DecisionSetRecord, new: DecisionSetRecord) -> None:
        self._sets[self._sets.index(old)] = new

    @staticmethod
    def _validate_items(items: tuple[DecisionItemRecord, ...]) -> None:
        codes = [item.stock_code for item in items]
        ranks = [item.rank for item in items]
        if not 1 <= len(items) <= 5 or len(codes) != len(set(codes)):
            raise DecisionValidationError("decision must contain one to five unique stocks")
        if sorted(ranks) != list(range(1, len(items) + 1)):
            raise DecisionValidationError("decision ranks must be contiguous")


def _validated_selection(
    source: DecisionSetRecord,
    source_type: DecisionType,
    command: ApprovalCommand,
    allowed_codes: set[str],
) -> tuple[str, ...]:
    if not command.reason.strip():
        raise DecisionValidationError("approval reason is required")
    expected_action = {
        DecisionType.RULE: ApprovalAction.ACCEPT_RULE,
        DecisionType.AI: ApprovalAction.ACCEPT_AI,
    }[source_type]
    source_codes = tuple(item.stock_code for item in source.items)
    if command.action is ApprovalAction.REJECT:
        if command.selected_codes:
            raise DecisionValidationError("rejection cannot contain selected codes")
        return ()
    if command.action is expected_action:
        if command.selected_codes != source_codes:
            raise DecisionValidationError("accept action must preserve the source decision")
        return source_codes
    if command.action is not ApprovalAction.MODIFY:
        raise DecisionValidationError("approval action does not match the source decision")
    if len(command.selected_codes) != len(source.items):
        raise DecisionValidationError("manual modification must preserve decision capacity")
    if len(command.selected_codes) != len(set(command.selected_codes)):
        raise DecisionValidationError("selected stock codes must be unique")
    if not set(command.selected_codes) <= allowed_codes:
        raise DecisionValidationError("manual selection contains an ineligible stock")
    return command.selected_codes


def _approval_payload(source_type: DecisionType, command: ApprovalCommand) -> str:
    return json.dumps(
        {
            "source_type": source_type.value,
            "action": command.action.value,
            "decision_version": command.decision_version,
            "selected_codes": command.selected_codes,
            "reason": command.reason,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
