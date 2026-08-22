import statistics
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol, cast

from sqlalchemy import select, union
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from pawe_api.contracts import (
    DataQuality,
    WeeklyReviewItem,
    WeeklyReviewResponse,
)
from pawe_api.db import models
from pawe_api.evaluation.weekly import WeeklyBar, WeeklyPerformance, evaluate_weekly_path


@dataclass(frozen=True, slots=True)
class ReviewTarget:
    stock_id: int
    stock_code: str
    stock_name: str
    rank: int
    bars: tuple[WeeklyBar, ...]
    industry_return: float | None = None


@dataclass(frozen=True, slots=True)
class EvaluatedTarget:
    target: ReviewTarget
    performance: WeeklyPerformance
    benchmark_return: float | None


@dataclass(frozen=True, slots=True)
class ComputedWeeklyReview:
    week_id: date
    source_type: str
    source_version: int
    rule_version: str
    status: str
    entry_trade_date: date
    final_trade_date: date
    as_of: datetime
    generated_at: datetime
    quality: DataQuality
    aggregate: dict[str, object]
    summary: str
    report_markdown: str
    warnings: tuple[str, ...]
    items: tuple[EvaluatedTarget, ...]


class WeeklyReviewApplication(Protocol):
    async def list_archive_weeks(self) -> list[date]: ...

    async def list_all(self) -> list[WeeklyReviewResponse]: ...

    async def list_week(self, week_id: date) -> list[WeeklyReviewResponse]: ...

    async def latest(self) -> WeeklyReviewResponse | None: ...


def archive_week_statement() -> Select[Any]:
    archived = union(
        select(models.WeeklyReview.week_id).where(models.WeeklyReview.is_active.is_(True)),
        select(models.DailyBrief.week_id).where(
            models.DailyBrief.status == "published",
            models.DailyBrief.is_active.is_(True),
        ),
        select(models.DecisionSet.week_id).where(
            models.DecisionSet.type == "published",
            models.DecisionSet.status == "published",
            models.DecisionSet.is_active.is_(True),
        ),
        select(models.ReplayRun.week_id).where(
            models.ReplayRun.status == "succeeded",
            models.ReplayRun.requested_stage == "weekly_review",
        ),
    ).subquery()
    return select(archived.c.week_id).order_by(archived.c.week_id.desc())


class SqlWeeklyReviewApplication:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_archive_weeks(self) -> list[date]:
        return list(await self.session.scalars(archive_week_statement()))

    async def list_all(self) -> list[WeeklyReviewResponse]:
        rows = list(
            await self.session.scalars(
                select(models.WeeklyReview)
                .where(models.WeeklyReview.is_active.is_(True))
                .order_by(
                    models.WeeklyReview.week_id.desc(),
                    models.WeeklyReview.source_type,
                    models.WeeklyReview.source_version.desc(),
                )
            )
        )
        return [await self._response(row) for row in rows]

    async def list_week(self, week_id: date) -> list[WeeklyReviewResponse]:
        rows = list(
            await self.session.scalars(
                select(models.WeeklyReview)
                .where(models.WeeklyReview.week_id == week_id)
                .order_by(models.WeeklyReview.source_type, models.WeeklyReview.source_version)
            )
        )
        return [await self._response(row) for row in rows]

    async def latest(self) -> WeeklyReviewResponse | None:
        row = await self.session.scalar(
            select(models.WeeklyReview)
            .where(models.WeeklyReview.is_active.is_(True))
            .order_by(models.WeeklyReview.week_id.desc(), models.WeeklyReview.generated_at.desc())
            .limit(1)
        )
        return await self._response(row) if row is not None else None

    async def persist(
        self,
        computed: ComputedWeeklyReview,
        *,
        replay_run_id: uuid.UUID | None = None,
        decision_set_id: uuid.UUID | None = None,
    ) -> WeeklyReviewResponse:
        async with self.session.begin():
            existing = await self.session.scalar(
                select(models.WeeklyReview).where(
                    models.WeeklyReview.week_id == computed.week_id,
                    models.WeeklyReview.source_type == computed.source_type,
                    models.WeeklyReview.source_version == computed.source_version,
                    models.WeeklyReview.rule_version == computed.rule_version,
                )
            )
            if existing is not None:
                return await self._response(existing)
            review = models.WeeklyReview(
                id=uuid.uuid4(),
                week_id=computed.week_id,
                source_type=computed.source_type,
                source_version=computed.source_version,
                rule_version=computed.rule_version,
                decision_set_id=decision_set_id,
                replay_run_id=replay_run_id,
                status=computed.status,
                entry_trade_date=computed.entry_trade_date,
                final_trade_date=computed.final_trade_date,
                as_of=computed.as_of,
                generated_at=computed.generated_at,
                quality=computed.quality.value,
                aggregate=computed.aggregate,
                summary=computed.summary,
                report_markdown=computed.report_markdown,
                warnings=list(computed.warnings),
                is_active=True,
            )
            self.session.add(review)
            await self.session.flush()
            for item in computed.items:
                performance = item.performance
                benchmark_excess = (
                    performance.week_close_return - item.benchmark_return
                    if item.benchmark_return is not None
                    else None
                )
                industry_excess = (
                    performance.week_close_return - item.target.industry_return
                    if item.target.industry_return is not None
                    else None
                )
                self.session.add(
                    models.WeeklyReviewItem(
                        id=uuid.uuid4(),
                        weekly_review_id=review.id,
                        stock_id=item.target.stock_id,
                        rank=item.target.rank,
                        entry_price=Decimal(str(performance.entry_price)),
                        week_high_return=Decimal(str(performance.week_high_return)),
                        week_close_return=Decimal(str(performance.week_close_return)),
                        max_drawdown_from_entry=Decimal(str(performance.max_drawdown_from_entry)),
                        max_peak_to_trough_drawdown=Decimal(
                            str(performance.max_peak_to_trough_drawdown)
                        ),
                        target_touched=performance.target_touched,
                        target_touch_date=performance.target_touch_date,
                        drawdown_before_touch=(
                            Decimal(str(performance.drawdown_before_touch))
                            if performance.drawdown_before_touch is not None
                            else None
                        ),
                        accessible_at_entry=performance.accessible_at_entry,
                        benchmark_return=(
                            Decimal(str(item.benchmark_return))
                            if item.benchmark_return is not None
                            else None
                        ),
                        benchmark_excess=(
                            Decimal(str(benchmark_excess)) if benchmark_excess is not None else None
                        ),
                        industry_return=(
                            Decimal(str(item.target.industry_return))
                            if item.target.industry_return is not None
                            else None
                        ),
                        industry_excess=(
                            Decimal(str(industry_excess)) if industry_excess is not None else None
                        ),
                    )
                )
            await self.session.flush()
            return await self._response(review)

    async def _response(self, review: models.WeeklyReview) -> WeeklyReviewResponse:
        rows = list(
            (
                await self.session.execute(
                    select(models.WeeklyReviewItem, models.Stock)
                    .join(models.Stock, models.Stock.id == models.WeeklyReviewItem.stock_id)
                    .where(models.WeeklyReviewItem.weekly_review_id == review.id)
                    .order_by(models.WeeklyReviewItem.rank)
                )
            ).all()
        )
        return WeeklyReviewResponse(
            id=str(review.id),
            week_id=review.week_id,
            source_type=review.source_type,
            source_version=review.source_version,
            rule_version=review.rule_version,
            status=review.status,
            entry_trade_date=review.entry_trade_date,
            final_trade_date=review.final_trade_date,
            as_of=review.as_of,
            generated_at=review.generated_at,
            quality=review.quality,
            aggregate=review.aggregate,
            summary=review.summary,
            warnings=review.warnings,
            items=[_item_response(item, stock) for item, stock in rows],
        )


def compute_weekly_review(
    *,
    week_id: date,
    source_type: str,
    source_version: int,
    rule_version: str,
    targets: tuple[ReviewTarget, ...],
    as_of: datetime,
    generated_at: datetime,
    quality: DataQuality,
    benchmark_return: float | None,
    warnings: tuple[str, ...] = (),
) -> ComputedWeeklyReview:
    if not targets:
        raise ValueError("weekly review requires at least one target")
    performances = tuple(
        EvaluatedTarget(
            target,
            evaluate_weekly_path(list(target.bars)),
            benchmark_return,
        )
        for target in targets
    )
    entry_dates = {item.performance.entry_trade_date for item in performances}
    final_dates = {item.target.bars[-1].trade_date for item in performances}
    if len(entry_dates) != 1 or len(final_dates) != 1:
        raise ValueError("weekly review targets do not share one complete trading week")
    aggregate = _aggregate(performances, benchmark_return)
    summary = _summary(aggregate, len(performances), warnings)
    return ComputedWeeklyReview(
        week_id=week_id,
        source_type=source_type,
        source_version=source_version,
        rule_version=rule_version,
        status="degraded" if warnings else "completed",
        entry_trade_date=next(iter(entry_dates)),
        final_trade_date=next(iter(final_dates)),
        as_of=as_of,
        generated_at=generated_at,
        quality=quality,
        aggregate=aggregate,
        summary=summary,
        report_markdown=_markdown(
            week_id,
            rule_version,
            performances,
            aggregate,
            summary,
            warnings,
        ),
        warnings=warnings,
        items=performances,
    )


def _aggregate(
    items: tuple[EvaluatedTarget, ...], benchmark_return: float | None
) -> dict[str, object]:
    highs = [item.performance.week_high_return for item in items]
    closes = [item.performance.week_close_return for item in items]
    drawdowns = [item.performance.max_drawdown_from_entry for item in items]
    strongest_index = max(range(len(items)), key=lambda index: highs[index])
    without_anchor = [value for index, value in enumerate(highs) if index != strongest_index]
    industry_excess = [
        item.performance.week_close_return - item.target.industry_return
        for item in items
        if item.target.industry_return is not None
    ]
    return {
        "item_count": len(items),
        "target_touched_count": sum(item.performance.target_touched for item in items),
        "target_touch_rate": statistics.mean(
            [float(item.performance.target_touched) for item in items]
        ),
        "average_week_high_return": statistics.mean(highs),
        "median_week_high_return": statistics.median(highs),
        "average_week_close_return": statistics.mean(closes),
        "median_week_close_return": statistics.median(closes),
        "average_max_drawdown": statistics.mean(drawdowns),
        "worst_max_drawdown": min(drawdowns),
        "average_high_without_strongest": (
            statistics.mean(without_anchor) if without_anchor else None
        ),
        "accessible_count": sum(item.performance.accessible_at_entry for item in items),
        "benchmark_return": benchmark_return,
        "average_benchmark_excess": (
            statistics.mean([value - benchmark_return for value in closes])
            if benchmark_return is not None
            else None
        ),
        "average_industry_excess": (statistics.mean(industry_excess) if industry_excess else None),
    }


def _summary(aggregate: dict[str, object], item_count: int, warnings: tuple[str, ...]) -> str:
    touched = cast(int, aggregate["target_touched_count"])
    average_high = cast(float, aggregate["average_week_high_return"])
    average_close = cast(float, aggregate["average_week_close_return"])
    worst_drawdown = cast(float, aggregate["worst_max_drawdown"])
    conclusion = (
        "本周规则组合达到核心情景。"
        if touched / item_count >= 0.4 and average_high >= 0.10
        else "本周规则组合未达到核心情景，需要进入错误归因。"
    )
    degraded = " 本次为研究性回放，存在降级项。" if warnings else ""
    return (
        f"{item_count}只标的中{touched}只触达10%，平均周内最高"
        f"{average_high:.1%}，平均周终收盘{average_close:.1%}，"
        f"最差入口回撤{worst_drawdown:.1%}。{conclusion}{degraded}"
    )


def _markdown(
    week_id: date,
    rule_version: str,
    items: tuple[EvaluatedTarget, ...],
    aggregate: dict[str, object],
    summary: str,
    warnings: tuple[str, ...],
) -> str:
    lines = [
        f"# {week_id.isoformat()} 周终复盘",
        "",
        f"- 规则版本：{rule_version}",
        f"- 结果摘要：{summary}",
        "- 口径：当周首个交易日开盘为入口，最后交易日收盘为周终。",
        "",
        "| 排名 | 代码 | 名称 | 周内最高 | 周终收盘 | 最大回撤 | 触达10% |",
        "|---:|---|---|---:|---:|---:|---|",
    ]
    for item in items:
        performance = item.performance
        lines.append(
            f"| {item.target.rank} | {item.target.stock_code} | "
            f"{item.target.stock_name} | {performance.week_high_return:.2%} | "
            f"{performance.week_close_return:.2%} | "
            f"{performance.max_drawdown_from_entry:.2%} | "
            f"{'是' if performance.target_touched else '否'} |"
        )
    lines.extend(["", "## 组合指标", ""])
    for key, value in aggregate.items():
        lines.append(f"- `{key}`：{value}")
    if warnings:
        lines.extend(["", "## 回放限制", ""] + [f"- {warning}" for warning in warnings])
    lines.extend(["", "> 仅供研究回放，不构成收益保证或交易指令。", ""])
    return "\n".join(lines)


def _item_response(item: models.WeeklyReviewItem, stock: models.Stock) -> WeeklyReviewItem:
    return WeeklyReviewItem(
        stock_code=stock.code,
        stock_name=stock.name,
        rank=item.rank,
        entry_price=float(item.entry_price),
        week_high_return=float(item.week_high_return),
        week_close_return=float(item.week_close_return),
        max_drawdown_from_entry=float(item.max_drawdown_from_entry),
        max_peak_to_trough_drawdown=float(item.max_peak_to_trough_drawdown),
        target_touched=item.target_touched,
        target_touch_date=item.target_touch_date,
        drawdown_before_touch=(
            float(item.drawdown_before_touch) if item.drawdown_before_touch is not None else None
        ),
        accessible_at_entry=item.accessible_at_entry,
        benchmark_return=(
            float(item.benchmark_return) if item.benchmark_return is not None else None
        ),
        benchmark_excess=(
            float(item.benchmark_excess) if item.benchmark_excess is not None else None
        ),
        industry_return=(float(item.industry_return) if item.industry_return is not None else None),
        industry_excess=(float(item.industry_excess) if item.industry_excess is not None else None),
    )
