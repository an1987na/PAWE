import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from pawe_api.briefs.repository import _brief_inputs
from pawe_api.briefs.service import build_deterministic_brief_item
from pawe_api.contracts import (
    DailyBriefItem,
    StockSearchResult,
    WatchlistDailyBrief,
    WatchlistItemResponse,
    WatchlistWeeklyReview,
    WeeklyReviewItem,
)
from pawe_api.data.calendar import SHANGHAI
from pawe_api.db import models
from pawe_api.evaluation.weekly import WeeklyBar, evaluate_weekly_path
from pawe_api.features.market_snapshot import build_stored_daily_brief_observation


class WatchlistError(ValueError):
    pass


class SqlWatchlistApplication:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search(self, user_id: uuid.UUID, query: str) -> list[StockSearchResult]:
        normalized = query.strip()
        if not normalized:
            return []
        followed = select(models.UserWatchlistItem.stock_id).where(
            models.UserWatchlistItem.user_id == user_id,
            models.UserWatchlistItem.removed_at.is_(None),
        )
        rows = list(
            await self.session.scalars(
                select(models.Stock)
                .where(
                    models.Stock.status == "active",
                    models.Stock.exchange.in_(("SSE", "SZSE")),
                    or_(
                        models.Stock.code.ilike(f"%{normalized}%"),
                        models.Stock.name.ilike(f"%{normalized}%"),
                    ),
                )
                .order_by(models.Stock.code)
                .limit(20)
            )
        )
        followed_ids = set(await self.session.scalars(followed))
        return [
            StockSearchResult(
                stock_code=stock.code,
                stock_name=stock.name,
                exchange=stock.exchange,
                board=stock.board,
                already_followed=stock.id in followed_ids,
            )
            for stock in rows
        ]

    async def list_active(self, user_id: uuid.UUID) -> list[WatchlistItemResponse]:
        result = await self.session.execute(
            select(models.UserWatchlistItem, models.Stock)
            .join(models.Stock, models.Stock.id == models.UserWatchlistItem.stock_id)
            .where(
                models.UserWatchlistItem.user_id == user_id,
                models.UserWatchlistItem.removed_at.is_(None),
            )
            .order_by(models.UserWatchlistItem.added_at)
        )
        return [_watch_response(item, stock) for item, stock in result.all()]

    async def add(
        self, user_id: uuid.UUID, stock_code: str, *, now: datetime
    ) -> WatchlistItemResponse:
        local_now = now.astimezone(SHANGHAI)
        async with self.session.begin():
            await self.session.scalar(
                select(models.User.id).where(models.User.id == user_id).with_for_update()
            )
            stock = await self.session.scalar(
                select(models.Stock).where(
                    models.Stock.code == stock_code,
                    models.Stock.status == "active",
                    models.Stock.exchange.in_(("SSE", "SZSE")),
                )
            )
            if stock is None:
                raise WatchlistError("未找到有效的A股标的")
            existing = await self.session.scalar(
                select(models.UserWatchlistItem).where(
                    models.UserWatchlistItem.user_id == user_id,
                    models.UserWatchlistItem.stock_id == stock.id,
                    models.UserWatchlistItem.removed_at.is_(None),
                )
            )
            if existing is not None:
                return _watch_response(existing, stock)
            active_count = int(
                await self.session.scalar(
                    select(func.count(models.UserWatchlistItem.id)).where(
                        models.UserWatchlistItem.user_id == user_id,
                        models.UserWatchlistItem.removed_at.is_(None),
                    )
                )
                or 0
            )
            if active_count >= 5:
                raise WatchlistError("每位用户最多关注5只标的")
            effective_from = await self._effective_from(local_now)
            item = models.UserWatchlistItem(
                id=uuid.uuid4(),
                user_id=user_id,
                stock_id=stock.id,
                added_at=now.astimezone(UTC),
                effective_from=effective_from,
                removed_at=None,
            )
            self.session.add(item)
            await self.session.flush()
            return _watch_response(item, stock)

    async def remove(self, user_id: uuid.UUID, stock_code: str, *, now: datetime) -> bool:
        async with self.session.begin():
            item = await self.session.scalar(
                select(models.UserWatchlistItem)
                .join(models.Stock, models.Stock.id == models.UserWatchlistItem.stock_id)
                .where(
                    models.UserWatchlistItem.user_id == user_id,
                    models.UserWatchlistItem.removed_at.is_(None),
                    models.Stock.code == stock_code,
                )
                .with_for_update()
            )
            if item is None:
                return False
            item.removed_at = now.astimezone(UTC)
            return True

    async def _effective_from(self, local_now: datetime) -> date:
        today_open = await self.session.get(models.TradingCalendar, local_now.date())
        if today_open is not None and today_open.is_open and local_now.time() < time(9, 30):
            return local_now.date()
        next_open = await self.session.scalar(
            select(models.TradingCalendar.trade_date)
            .where(
                models.TradingCalendar.trade_date > local_now.date(),
                models.TradingCalendar.is_open.is_(True),
            )
            .order_by(models.TradingCalendar.trade_date)
            .limit(1)
        )
        if next_open is None:
            raise WatchlistError("缺少后续交易日历，暂时无法加入自选")
        return next_open

    async def list_daily(self, user_id: uuid.UUID, week_id: date) -> list[WatchlistDailyBrief]:
        result = await self.session.execute(
            select(models.UserWatchlistDailyItem, models.Stock)
            .join(models.Stock, models.Stock.id == models.UserWatchlistDailyItem.stock_id)
            .where(
                models.UserWatchlistDailyItem.user_id == user_id,
                models.UserWatchlistDailyItem.week_id == week_id,
            )
            .order_by(models.UserWatchlistDailyItem.trade_date, models.Stock.code)
        )
        grouped: dict[date, list[DailyBriefItem]] = {}
        for row, _stock in result.all():
            grouped.setdefault(row.trade_date, []).append(
                DailyBriefItem.model_validate(row.payload)
            )
        return [
            WatchlistDailyBrief(week_id=week_id, trade_date=day, items=items)
            for day, items in grouped.items()
        ]

    async def list_weekly(self, user_id: uuid.UUID, week_id: date) -> WatchlistWeeklyReview | None:
        result = await self.session.execute(
            select(models.UserWatchlistWeeklyItem, models.Stock)
            .join(models.Stock, models.Stock.id == models.UserWatchlistWeeklyItem.stock_id)
            .where(
                models.UserWatchlistWeeklyItem.user_id == user_id,
                models.UserWatchlistWeeklyItem.week_id == week_id,
            )
            .order_by(models.Stock.code)
        )
        rows = result.all()
        if not rows:
            return None
        return WatchlistWeeklyReview(
            week_id=week_id,
            generated_at=max(row.generated_at for row, _ in rows),
            items=[WeeklyReviewItem.model_validate(row.payload) for row, _ in rows],
        )


async def generate_watchlist_daily_items(
    session: AsyncSession, trade_date: date, *, fetched_at: datetime
) -> int:
    cutoff = datetime.combine(trade_date, time(15, 30), tzinfo=SHANGHAI).astimezone(UTC)
    result = await session.execute(
        select(models.UserWatchlistItem, models.Stock)
        .join(models.Stock, models.Stock.id == models.UserWatchlistItem.stock_id)
        .where(models.UserWatchlistItem.effective_from <= trade_date)
    )
    generated = 0
    async with session.begin_nested():
        for membership, stock in result.all():
            if membership.removed_at is not None and membership.removed_at <= cutoff:
                continue
            existing = await session.scalar(
                select(models.UserWatchlistDailyItem.id).where(
                    models.UserWatchlistDailyItem.user_id == membership.user_id,
                    models.UserWatchlistDailyItem.trade_date == trade_date,
                    models.UserWatchlistDailyItem.stock_id == stock.id,
                )
            )
            if existing is not None:
                continue
            observation = await build_stored_daily_brief_observation(
                session, stock, as_of=trade_date, snapshot_cutoff=fetched_at
            )
            target, market = _brief_inputs(
                stock.code,
                stock.name,
                observation.payload,
                week_id=membership.effective_from,
                trade_date=trade_date,
                quality=observation.quality,
            )
            item = build_deterministic_brief_item(target, market)
            session.add(
                models.UserWatchlistDailyItem(
                    id=uuid.uuid4(),
                    user_id=membership.user_id,
                    watchlist_item_id=membership.id,
                    stock_id=stock.id,
                    week_id=trade_date - timedelta(days=trade_date.weekday()),
                    trade_date=trade_date,
                    as_of=datetime.combine(trade_date, time(15), tzinfo=SHANGHAI),
                    fetched_at=fetched_at,
                    quality=observation.quality.value,
                    payload=item.model_dump(mode="json"),
                )
            )
            generated += 1
    await session.commit()
    return generated


async def generate_watchlist_weekly_items(
    session: AsyncSession, week_id: date, *, generated_at: datetime
) -> int:
    open_dates = list(
        await session.scalars(
            select(models.TradingCalendar.trade_date)
            .where(
                models.TradingCalendar.trade_date >= week_id,
                models.TradingCalendar.trade_date <= week_id + timedelta(days=4),
                models.TradingCalendar.is_open.is_(True),
            )
            .order_by(models.TradingCalendar.trade_date)
        )
    )
    if len(open_dates) < 3:
        return 0
    result = await session.execute(
        select(models.UserWatchlistItem, models.Stock)
        .join(models.Stock, models.Stock.id == models.UserWatchlistItem.stock_id)
        .where(models.UserWatchlistItem.effective_from <= open_dates[-1])
    )
    generated = 0
    user_ranks: dict[uuid.UUID, int] = {}
    for membership, stock in result.all():
        entry_dates = [day for day in open_dates if day >= max(week_id, membership.effective_from)]
        if not entry_dates:
            continue
        final_dates = entry_dates
        if membership.removed_at is not None:
            final_dates = [
                day
                for day in entry_dates
                if membership.removed_at
                > datetime.combine(day, time(15, 30), tzinfo=SHANGHAI).astimezone(UTC)
            ]
        if not final_dates:
            continue
        existing = await session.scalar(
            select(models.UserWatchlistWeeklyItem.id).where(
                models.UserWatchlistWeeklyItem.user_id == membership.user_id,
                models.UserWatchlistWeeklyItem.week_id == week_id,
                models.UserWatchlistWeeklyItem.stock_id == stock.id,
            )
        )
        if existing is not None:
            continue
        observation = await build_stored_daily_brief_observation(
            session, stock, as_of=final_dates[-1], snapshot_cutoff=generated_at
        )
        bars = _payload_weekly_bars(observation.payload, set(final_dates))
        if not bars:
            continue
        performance = evaluate_weekly_path(bars)
        rank = user_ranks.get(membership.user_id, 0) + 1
        item = WeeklyReviewItem(
            stock_code=stock.code,
            stock_name=stock.name,
            rank=rank,
            entry_price=performance.entry_price,
            week_high_return=performance.week_high_return,
            week_close_return=performance.week_close_return,
            max_drawdown_from_entry=performance.max_drawdown_from_entry,
            max_peak_to_trough_drawdown=performance.max_peak_to_trough_drawdown,
            target_touched=performance.target_touched,
            target_touch_date=performance.target_touch_date,
            drawdown_before_touch=performance.drawdown_before_touch,
            accessible_at_entry=performance.accessible_at_entry,
        )
        user_ranks[membership.user_id] = rank
        session.add(
            models.UserWatchlistWeeklyItem(
                id=uuid.uuid4(),
                user_id=membership.user_id,
                watchlist_item_id=membership.id,
                stock_id=stock.id,
                week_id=week_id,
                generated_at=generated_at,
                quality=observation.quality.value,
                payload=item.model_dump(mode="json"),
            )
        )
        generated += 1
    await session.commit()
    return generated


def _payload_weekly_bars(payload: dict[str, object], dates: set[date]) -> list[WeeklyBar]:
    sources = payload.get("source_bars")
    if not isinstance(sources, dict):
        return []
    values = next((value for value in sources.values() if isinstance(value, list)), [])
    return [
        WeeklyBar(
            trade_date=date.fromisoformat(str(row["trade_date"])),
            open=float(str(row["open"])),
            high=float(str(row["high"])),
            low=float(str(row["low"])),
            close=float(str(row["close"])),
        )
        for row in values
        if isinstance(row, dict) and date.fromisoformat(str(row["trade_date"])) in dates
    ]


def _watch_response(item: models.UserWatchlistItem, stock: models.Stock) -> WatchlistItemResponse:
    return WatchlistItemResponse(
        id=str(item.id),
        stock_code=stock.code,
        stock_name=stock.name,
        exchange=stock.exchange,
        board=stock.board,
        added_at=item.added_at,
        effective_from=item.effective_from,
    )
