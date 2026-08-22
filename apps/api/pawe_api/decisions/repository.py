import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

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
    WeeklyStatus,
    WeekSummary,
)
from pawe_api.db import models
from pawe_api.decisions.ledger import DecisionConflictError, DecisionValidationError


class DecisionApplication(Protocol):
    async def current_published(self, today: date) -> WeekSummary | None: ...

    async def list_decisions(self, week_id: date) -> list[DecisionVersionResponse]: ...

    async def approve(self, week_id: date, request: ApprovalRequest) -> ApprovalResponse: ...

    async def publish(self, week_id: date, request: PublishRequest) -> DecisionVersionResponse: ...


class SqlDecisionApplication:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def current_published(self, today: date) -> WeekSummary | None:
        latest_open = await self.session.scalar(
            select(func.max(models.TradingCalendar.trade_date)).where(
                models.TradingCalendar.trade_date <= today,
                models.TradingCalendar.is_open.is_(True),
            )
        )
        display_date = latest_open or today
        week_start, week_end = decision_display_week_bounds(display_date)
        result = await self.session.execute(
            select(models.DecisionSet, models.Week)
            .join(models.Week, models.Week.week_id == models.DecisionSet.week_id)
            .where(
                models.DecisionSet.type == "published",
                models.DecisionSet.status == "published",
                models.DecisionSet.is_active.is_(True),
                models.DecisionSet.week_id >= week_start,
                models.DecisionSet.week_id <= week_end,
            )
            .order_by(models.DecisionSet.week_id.desc(), models.DecisionSet.version.desc())
            .limit(1)
        )
        row = result.one_or_none()
        if row is None:
            return None
        decision, week = row
        item_rows = (
            await self.session.execute(
                select(models.DecisionItem, models.Stock)
                .join(models.Stock, models.Stock.id == models.DecisionItem.stock_id)
                .where(models.DecisionItem.decision_set_id == decision.id)
                .order_by(models.DecisionItem.rank)
            )
        ).all()
        stock_ids = [stock.id for _, stock in item_rows]
        audit_rows = (
            await self.session.execute(
                select(models.Candidate, models.WeeklyFeature)
                .join(
                    models.WeeklyFeature,
                    (models.WeeklyFeature.snapshot_id == models.Candidate.snapshot_id)
                    & (models.WeeklyFeature.stock_id == models.Candidate.stock_id),
                )
                .where(
                    models.Candidate.week_id == decision.week_id,
                    models.Candidate.stock_id.in_(stock_ids),
                )
            )
        ).all()
        audit_by_stock_id = {
            candidate.stock_id: (candidate, feature) for candidate, feature in audit_rows
        }
        items = [
            DecisionItem(
                stock_code=stock.code,
                stock_name=stock.name,
                rank=item.rank,
                target_return=float(item.target_return),
                confidence=Confidence(item.confidence),
                summary=item.summary,
                primary_risk=item.primary_risk,
                primary_sector=_audit_sector(audit_by_stock_id.get(stock.id)),
                rule_score=_audit_score(audit_by_stock_id.get(stock.id)),
                selection_reasons=_selection_reasons(audit_by_stock_id.get(stock.id)),
                score_breakdown=_score_breakdown(audit_by_stock_id.get(stock.id)),
            )
            for item, stock in item_rows
        ]
        confidence_order = {Confidence.HIGH: 2, Confidence.MEDIUM: 1, Confidence.LOW: 0}
        overall_confidence = min(
            (item.confidence for item in items),
            key=confidence_order.__getitem__,
            default=Confidence.LOW,
        )
        return WeekSummary(
            week_id=decision.week_id,
            status=WeeklyStatus.PUBLISHED,
            market_state=MarketState(week.market_state),
            decision_version=decision.version,
            confidence=overall_confidence,
            shortage=decision.shortage,
            shortage_reason=decision.shortage_reason,
            items=items,
        )

    async def list_decisions(self, week_id: date) -> list[DecisionVersionResponse]:
        result = await self.session.execute(
            select(models.DecisionSet)
            .where(models.DecisionSet.week_id == week_id)
            .order_by(models.DecisionSet.type, models.DecisionSet.version)
        )
        return [await self._decision_response(decision) for decision in result.scalars()]

    async def approve(self, week_id: date, request: ApprovalRequest) -> ApprovalResponse:
        async with self.session.begin():
            existing = await self._existing_approval(week_id, request)
            if existing is not None:
                return existing
            source = await self._approval_source(week_id, request)
            source_items = await self._decision_items(source.id)
            source_codes = tuple(item.stock_code for item in source_items)
            selected_codes = tuple(request.selected_codes)
            self._validate_action(request, source_codes)
            if request.action is ApprovalAction.MODIFY:
                await self._validate_manual_selection(week_id, selected_codes, len(source_codes))

            approved: models.DecisionSet | None = None
            if request.action is not ApprovalAction.REJECT:
                approved = await self._create_approved_decision(
                    source,
                    source_items,
                    selected_codes,
                    created_at=datetime.now(UTC),
                )
                source.status = "approved"
                await self.session.execute(
                    update(models.Week)
                    .where(models.Week.week_id == week_id)
                    .values(status="approved")
                )
            approval = models.Approval(
                id=uuid.uuid4(),
                week_id=week_id,
                source_decision_set_id=source.id,
                approved_decision_set_id=approved.id if approved is not None else None,
                decision_version=request.decision_version,
                action=request.action.value,
                selected_codes=list(selected_codes),
                reason=request.reason,
                idempotency_key=request.idempotency_key,
                created_at=datetime.now(UTC),
            )
            self.session.add(approval)
            await self.session.flush()
            approved_response = (
                await self._decision_response(approved) if approved is not None else None
            )
            return ApprovalResponse(
                approval_id=str(approval.id),
                action=request.action,
                approved_decision=approved_response,
            )

    async def publish(self, week_id: date, request: PublishRequest) -> DecisionVersionResponse:
        async with self.session.begin():
            existing = await self.session.scalar(
                select(models.PublicationEvent).where(
                    models.PublicationEvent.week_id == week_id,
                    models.PublicationEvent.idempotency_key == request.idempotency_key,
                )
            )
            if existing is not None:
                if existing.decision_version != request.decision_version:
                    raise DecisionConflictError("publish idempotency key was reused")
                decision = await self.session.get(models.DecisionSet, existing.decision_set_id)
                assert decision is not None
                return await self._decision_response(decision)

            decision = await self.session.scalar(
                select(models.DecisionSet)
                .where(
                    models.DecisionSet.week_id == week_id,
                    models.DecisionSet.type == "published",
                    models.DecisionSet.version == request.decision_version,
                    models.DecisionSet.is_active.is_(True),
                )
                .with_for_update()
            )
            if decision is None:
                raise DecisionConflictError("approved decision version does not exist")
            if decision.status not in {"approved", "published"}:
                raise DecisionValidationError("only an approved decision can be published")
            published_at = datetime.now(UTC)
            if decision.status == "approved":
                decision.status = "published"
                decision.published_at = published_at
            event = models.PublicationEvent(
                id=uuid.uuid4(),
                week_id=week_id,
                decision_set_id=decision.id,
                decision_version=decision.version,
                idempotency_key=request.idempotency_key,
                created_at=published_at,
            )
            self.session.add(event)
            await self.session.execute(
                update(models.Week).where(models.Week.week_id == week_id).values(status="published")
            )
            await self.session.flush()
            return await self._decision_response(decision)

    async def _existing_approval(
        self, week_id: date, request: ApprovalRequest
    ) -> ApprovalResponse | None:
        approval = await self.session.scalar(
            select(models.Approval).where(
                models.Approval.week_id == week_id,
                models.Approval.idempotency_key == request.idempotency_key,
            )
        )
        if approval is None:
            return None
        if (
            approval.decision_version != request.decision_version
            or approval.action != request.action.value
            or approval.selected_codes != request.selected_codes
            or approval.reason != request.reason
        ):
            raise DecisionConflictError("approval idempotency key was reused")
        approved = (
            await self.session.get(models.DecisionSet, approval.approved_decision_set_id)
            if approval.approved_decision_set_id is not None
            else None
        )
        return ApprovalResponse(
            approval_id=str(approval.id),
            action=request.action,
            approved_decision=(
                await self._decision_response(approved) if approved is not None else None
            ),
        )

    async def _approval_source(self, week_id: date, request: ApprovalRequest) -> models.DecisionSet:
        source = await self.session.scalar(
            select(models.DecisionSet)
            .where(
                models.DecisionSet.week_id == week_id,
                models.DecisionSet.type == request.source_type,
                models.DecisionSet.is_active.is_(True),
            )
            .with_for_update()
        )
        if source is None:
            raise DecisionConflictError("active approval source does not exist")
        if source.version != request.decision_version:
            raise DecisionConflictError("approval decision version is stale")
        if source.status != "awaiting_approval":
            raise DecisionValidationError("decision is not awaiting approval")
        return source

    async def _create_approved_decision(
        self,
        source: models.DecisionSet,
        source_items: list[DecisionVersionItem],
        selected_codes: tuple[str, ...],
        *,
        created_at: datetime,
    ) -> models.DecisionSet:
        await self.session.execute(
            update(models.DecisionSet)
            .where(
                models.DecisionSet.week_id == source.week_id,
                models.DecisionSet.type == "published",
                models.DecisionSet.is_active.is_(True),
            )
            .values(is_active=False, status="superseded")
        )
        maximum_version = await self.session.scalar(
            select(func.max(models.DecisionSet.version)).where(
                models.DecisionSet.week_id == source.week_id,
                models.DecisionSet.type == "published",
            )
        )
        version = (maximum_version or 0) + 1
        fingerprint = hashlib.sha256(
            f"{source.fingerprint}:{version}:{','.join(selected_codes)}".encode()
        ).hexdigest()
        approved = models.DecisionSet(
            id=uuid.uuid4(),
            source_decision_set_id=source.id,
            week_id=source.week_id,
            type="published",
            version=version,
            status="approved",
            fingerprint=fingerprint,
            shortage=len(selected_codes) < 5,
            shortage_reason=(
                f"approved source capacity is {len(selected_codes)}"
                if len(selected_codes) < 5
                else None
            ),
            is_active=True,
            created_at=created_at,
            published_at=None,
        )
        self.session.add(approved)
        await self.session.flush()
        stocks_result = await self.session.execute(
            select(models.Stock).where(models.Stock.code.in_(selected_codes))
        )
        stocks = {stock.code: stock for stock in stocks_result.scalars()}
        if set(selected_codes) != stocks.keys():
            raise DecisionValidationError("selected stock master data is incomplete")
        source_by_code = {item.stock_code: item for item in source_items}
        for rank, code in enumerate(selected_codes, start=1):
            source_item = source_by_code.get(code)
            self.session.add(
                models.DecisionItem(
                    id=uuid.uuid4(),
                    decision_set_id=approved.id,
                    stock_id=stocks[code].id,
                    rank=rank,
                    role="source" if source_item is not None else "manual",
                    target_return=Decimal("0.10"),
                    confidence="medium" if source_item is not None else "low",
                    summary=(
                        "沿用已确认来源决策" if source_item is not None else "人工从合格候选池换入"
                    ),
                    primary_risk="需查看候选审计与证据详情",
                )
            )
        await self.session.flush()
        return approved

    async def _validate_manual_selection(
        self, week_id: date, selected_codes: tuple[str, ...], expected_capacity: int
    ) -> None:
        if len(selected_codes) != expected_capacity:
            raise DecisionValidationError("manual modification must preserve decision capacity")
        result = await self.session.execute(
            select(models.Stock.code)
            .join(models.Candidate, models.Candidate.stock_id == models.Stock.id)
            .where(
                models.Candidate.week_id == week_id,
                models.Candidate.bucket == "eligible",
                models.Stock.code.in_(selected_codes),
            )
        )
        if set(result.scalars()) != set(selected_codes):
            raise DecisionValidationError("manual selection contains an ineligible stock")

    @staticmethod
    def _validate_action(request: ApprovalRequest, source_codes: tuple[str, ...]) -> None:
        expected = {
            "rule": ApprovalAction.ACCEPT_RULE,
            "ai": ApprovalAction.ACCEPT_AI,
        }[request.source_type]
        if request.action is expected and tuple(request.selected_codes) != source_codes:
            raise DecisionValidationError("accept action must preserve source decision")
        if request.action not in {expected, ApprovalAction.MODIFY, ApprovalAction.REJECT}:
            raise DecisionValidationError("approval action does not match source type")

    async def _decision_items(self, decision_set_id: uuid.UUID) -> list[DecisionVersionItem]:
        result = await self.session.execute(
            select(models.DecisionItem, models.Stock)
            .join(models.Stock, models.Stock.id == models.DecisionItem.stock_id)
            .where(models.DecisionItem.decision_set_id == decision_set_id)
            .order_by(models.DecisionItem.rank)
        )
        return [
            DecisionVersionItem(stock_code=stock.code, stock_name=stock.name, rank=item.rank)
            for item, stock in result.all()
        ]

    async def _decision_response(self, decision: models.DecisionSet) -> DecisionVersionResponse:
        source = (
            await self.session.get(models.DecisionSet, decision.source_decision_set_id)
            if decision.source_decision_set_id is not None
            else None
        )
        return DecisionVersionResponse(
            week_id=decision.week_id,
            decision_type=decision.type,
            version=decision.version,
            status=decision.status,
            fingerprint=decision.fingerprint,
            source_type=source.type if source is not None else None,
            source_version=source.version if source is not None else None,
            items=await self._decision_items(decision.id),
        )


def natural_week_bounds(today: date) -> tuple[date, date]:
    week_start = today - timedelta(days=today.weekday())
    return week_start, week_start + timedelta(days=6)


def decision_display_week_bounds(today: date) -> tuple[date, date]:
    """Display only the trading week containing the latest open session."""
    week_start, _ = natural_week_bounds(today)
    return week_start, week_start + timedelta(days=6)


def _audit_sector(
    audit: tuple[models.Candidate, models.WeeklyFeature] | None,
) -> str | None:
    if audit is None:
        return None
    value = audit[1].payload.get("primary_sector")
    return _sector_label(str(value)) if value is not None else None


def _audit_score(
    audit: tuple[models.Candidate, models.WeeklyFeature] | None,
) -> float | None:
    return float(audit[0].rule_score) if audit is not None else None


def _score_breakdown(
    audit: tuple[models.Candidate, models.WeeklyFeature] | None,
) -> dict[str, float]:
    if audit is None:
        return {}
    return {key: float(value) for key, value in audit[0].score_breakdown.items()}


def _selection_reasons(
    audit: tuple[models.Candidate, models.WeeklyFeature] | None,
) -> list[str]:
    if audit is None:
        return ["该发布项缺少可展开的候选审计记录，请按数据降级处理。"]
    candidate, feature = audit
    payload = feature.payload
    reasons = [
        f"V9 硬约束通过，规则总分 {float(candidate.rule_score):.1f}。",
    ]
    sector = payload.get("primary_sector")
    if sector:
        reasons.append(f"所属主方向：{_sector_label(str(sector))}，满足本周领域与组合容量约束。")
    return_20d = payload.get("return_20d")
    above_ma20 = payload.get("above_ma20")
    if isinstance(return_20d, (int, float)):
        trend = "且位于 20 日均线上方" if above_ma20 is True else ""
        reasons.append(f"近 20 日涨幅 {return_20d * 100:.1f}%{trend}。")
    sector_up_ratio = payload.get("sector_up_ratio_5d")
    peer_count = payload.get("sector_positive_peer_count")
    if isinstance(sector_up_ratio, (int, float)) and isinstance(peer_count, int):
        reasons.append(
            f"方向近 5 日上涨覆盖率 {sector_up_ratio * 100:.1f}%，正收益同类 {peer_count} 只。"
        )
    avg_amount = payload.get("avg_amount_20d")
    if isinstance(avg_amount, (int, float)):
        reasons.append(
            f"近 20 日平均成交额约 {avg_amount / 100_000_000:.1f} 亿元，流动性门槛通过。"
        )
    quality = payload.get("data_quality")
    reasons.append(
        "行情与特征数据已通过双源验证。"
        if quality == "verified"
        else "行情或特征为单源可用，组合置信度已相应降级。"
    )
    return reasons[:8]


def _sector_label(value: str) -> str:
    return {
        "ai": "人工智能",
        "robotics": "机器人",
        "semiconductor": "半导体",
        "energy": "能源",
        "innovative_drug": "创新药",
        "medical_device": "医疗器械",
    }.get(value, value)
