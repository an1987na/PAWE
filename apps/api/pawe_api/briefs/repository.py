import statistics
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pawe_api.briefs.service import (
    DailyMarketSnapshot,
    PublishedTarget,
    build_deterministic_brief_item,
)
from pawe_api.contracts import DailyBrief, DailyBriefItem, DataQuality
from pawe_api.data.calendar import SHANGHAI
from pawe_api.db import models
from pawe_api.features.market_snapshot import build_stored_daily_brief_observation


class BriefGenerationError(RuntimeError):
    pass


class BriefApplication(Protocol):
    async def list_week(self, week_id: date) -> list[DailyBrief]: ...


class SqlBriefApplication:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_week(self, week_id: date) -> list[DailyBrief]:
        rows = list(
            await self.session.scalars(
                select(models.DailyBrief)
                .where(models.DailyBrief.week_id == week_id, models.DailyBrief.is_active.is_(True))
                .order_by(models.DailyBrief.trade_date, models.DailyBrief.version)
            )
        )
        return [await self._response(row) for row in rows]

    async def generate(self, trade_date: date, *, fetched_at: datetime) -> DailyBrief | None:
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("daily brief fetched_at must be timezone-aware")
        week_id = trade_date - timedelta(days=trade_date.weekday())
        async with self.session.begin():
            calendar = await self.session.get(models.TradingCalendar, trade_date)
            if calendar is None or not calendar.is_open:
                return None
            decision = await self.session.scalar(
                select(models.DecisionSet)
                .where(
                    models.DecisionSet.week_id == week_id,
                    models.DecisionSet.type == "published",
                    models.DecisionSet.status == "published",
                    models.DecisionSet.is_active.is_(True),
                )
                .with_for_update()
            )
            if decision is None:
                return None
            existing = await self.session.scalar(
                select(models.DailyBrief).where(
                    models.DailyBrief.week_id == week_id,
                    models.DailyBrief.trade_date == trade_date,
                    models.DailyBrief.decision_set_id == decision.id,
                    models.DailyBrief.is_active.is_(True),
                )
            )
            if existing is not None:
                return await self._response(existing)
            decision_rows = list(
                (
                    await self.session.execute(
                        select(models.DecisionItem, models.Stock)
                        .join(models.Stock, models.Stock.id == models.DecisionItem.stock_id)
                        .where(models.DecisionItem.decision_set_id == decision.id)
                        .order_by(models.DecisionItem.rank)
                    )
                ).all()
            )
            if not decision_rows:
                raise BriefGenerationError("published decision has no items")
            generated: list[tuple[models.DecisionItem, DailyBriefItem, DataQuality]] = []
            for decision_item, stock in decision_rows:
                observation = await build_stored_daily_brief_observation(
                    self.session,
                    stock,
                    as_of=trade_date,
                    snapshot_cutoff=fetched_at,
                )
                target, market = _brief_inputs(
                    stock.code,
                    stock.name,
                    observation.payload,
                    week_id=week_id,
                    trade_date=trade_date,
                    quality=observation.quality,
                )
                generated.append(
                    (
                        decision_item,
                        build_deterministic_brief_item(target, market),
                        observation.quality,
                    )
                )
            quality = min((item[2] for item in generated), key=_quality_rank)
            maximum_version = await self.session.scalar(
                select(func.max(models.DailyBrief.version)).where(
                    models.DailyBrief.week_id == week_id,
                    models.DailyBrief.trade_date == trade_date,
                    models.DailyBrief.decision_set_id == decision.id,
                )
            )
            await self.session.execute(
                update(models.DailyBrief)
                .where(
                    models.DailyBrief.week_id == week_id,
                    models.DailyBrief.trade_date == trade_date,
                    models.DailyBrief.decision_set_id == decision.id,
                    models.DailyBrief.is_active.is_(True),
                )
                .values(is_active=False)
            )
            brief = models.DailyBrief(
                id=uuid.uuid4(),
                week_id=week_id,
                trade_date=trade_date,
                decision_set_id=decision.id,
                version=(maximum_version or 0) + 1,
                status="published",
                as_of=datetime.combine(trade_date, time(15), tzinfo=SHANGHAI),
                fetched_at=fetched_at,
                quality=quality.value,
                ai_degraded=True,
                summary="确定性收盘简报；AI证据摘要尚未启用。",
                is_active=True,
            )
            self.session.add(brief)
            await self.session.flush()
            for decision_item, item, _ in generated:
                self.session.add(
                    models.DailyBriefItem(
                        id=uuid.uuid4(),
                        daily_brief_id=brief.id,
                        decision_item_id=decision_item.id,
                        daily_return=Decimal(str(item.daily_return)),
                        week_to_date_return=Decimal(str(item.week_to_date_return)),
                        week_high_return=Decimal(str(item.week_high_return)),
                        drawdown_from_week_high=Decimal(str(item.drawdown_from_week_high)),
                        distance_to_target=Decimal(str(item.distance_to_target)),
                        volume_activity=(
                            Decimal(str(item.volume_activity))
                            if item.volume_activity is not None
                            else None
                        ),
                        risk_status=item.risk_status.value,
                        summary=item.summary,
                        evidence_ids=item.evidence_ids,
                    )
                )
            await self.session.flush()
            return await self._response(brief)

    async def _response(self, brief: models.DailyBrief) -> DailyBrief:
        result = await self.session.execute(
            select(models.DailyBriefItem, models.DecisionItem, models.Stock)
            .join(
                models.DecisionItem,
                models.DecisionItem.id == models.DailyBriefItem.decision_item_id,
            )
            .join(models.Stock, models.Stock.id == models.DecisionItem.stock_id)
            .where(models.DailyBriefItem.daily_brief_id == brief.id)
            .order_by(models.DecisionItem.rank)
        )
        items = [
            DailyBriefItem(
                stock_code=stock.code,
                stock_name=stock.name,
                daily_return=float(item.daily_return),
                week_to_date_return=float(item.week_to_date_return),
                week_high_return=float(item.week_high_return),
                drawdown_from_week_high=float(item.drawdown_from_week_high),
                distance_to_target=float(item.distance_to_target),
                volume_activity=(
                    float(item.volume_activity) if item.volume_activity is not None else None
                ),
                risk_status=item.risk_status,
                summary=item.summary,
                evidence_ids=item.evidence_ids,
            )
            for item, _, stock in result.all()
        ]
        decision = await self.session.get(models.DecisionSet, brief.decision_set_id)
        if decision is None:
            raise BriefGenerationError("daily brief decision is missing")
        return DailyBrief(
            week_id=brief.week_id,
            trade_date=brief.trade_date,
            decision_version=decision.version,
            as_of=brief.as_of,
            fetched_at=brief.fetched_at,
            quality=brief.quality,
            ai_degraded=brief.ai_degraded,
            items=items,
        )


def _brief_inputs(
    code: str,
    name: str,
    payload: dict[str, Any],
    *,
    week_id: date,
    trade_date: date,
    quality: DataQuality,
) -> tuple[PublishedTarget, DailyMarketSnapshot]:
    source_bars = payload.get("source_bars")
    if not isinstance(source_bars, dict):
        raise BriefGenerationError("daily source bars are missing")
    candidates: list[tuple[int, list[dict[str, Any]]]] = []
    source_priority = {"tencent": 3, "eastmoney": 2, "sina": 1}
    for source, value in source_bars.items():
        if not isinstance(value, list):
            continue
        candidate_bars = [item for item in value if isinstance(item, dict)]
        if candidate_bars:
            priority = source_priority.get(str(source).split("+")[0], 0)
            candidates.append((priority, candidate_bars))
    bars: list[dict[str, Any]] | None = (
        max(candidates, key=lambda item: (len(item[1]), item[0]))[1]
        if candidates
        else None
    )
    if not bars:
        raise BriefGenerationError("no usable daily source bars")
    ordered = sorted(bars, key=lambda item: str(item.get("trade_date")))
    dated = [(date.fromisoformat(str(item["trade_date"])), item) for item in ordered]
    through_today = [(day, item) for day, item in dated if day <= trade_date]
    current_index = next(
        (index for index, (day, _) in enumerate(through_today) if day == trade_date),
        None,
    )
    if current_index is None or current_index == 0:
        raise BriefGenerationError("current or previous trading bar is missing")
    week_bars = [(day, item) for day, item in through_today if day >= week_id]
    if not week_bars:
        raise BriefGenerationError("weekly entry bar is missing")
    current = through_today[current_index][1]
    previous = through_today[current_index - 1][1]
    previous_volumes = [float(item["volume"]) for _, item in through_today[:current_index][-5:]]
    average_volume = statistics.mean(previous_volumes) if previous_volumes else None
    target = PublishedTarget(code, name, float(week_bars[0][1]["open"]))
    market = DailyMarketSnapshot(
        previous_close=float(previous["close"]),
        close=float(current["close"]),
        week_high=max(float(item["high"]) for _, item in week_bars),
        volume=float(current["volume"]),
        previous_five_day_average_volume=average_volume,
        quality=quality,
    )
    return target, market


def _quality_rank(quality: DataQuality) -> int:
    return {
        DataQuality.MISSING: 0,
        DataQuality.CONFLICTED: 1,
        DataQuality.DEGRADED: 2,
        DataQuality.SINGLE_SOURCE: 3,
        DataQuality.VERIFIED: 4,
    }[quality]
