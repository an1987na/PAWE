import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pawe_api.contracts import DataQuality
from pawe_api.data.baseline import (
    FEATURE_SCHEMA_VERSION,
    STATE_INPUT_SCHEMA_VERSION,
    canonical_payload_hash,
    serialize_market_state_input,
    serialize_rule_features,
)
from pawe_api.data.exchange_calendar import TradingCalendarWrite
from pawe_api.data.series import NormalizedDailyBar, ProviderDailySeries
from pawe_api.data.snapshot import FrozenSnapshot, SnapshotRecord, freeze_snapshot
from pawe_api.data.stock_master import StockMasterRecord
from pawe_api.db import models
from pawe_api.rules.market_state import MarketStateInput
from pawe_api.rules.models import RuleFeatures


@dataclass(frozen=True, slots=True)
class SnapshotInputRecord:
    record_key: str
    source: str
    as_of: datetime
    fetched_at: datetime
    quality: DataQuality
    payload: dict[str, Any]
    published_at: datetime | date | None = None
    adjustment: str | None = None


@dataclass(frozen=True, slots=True)
class StockMasterWriteResult:
    stocks_written: int
    classifications_created: int
    classifications_closed: int


@dataclass(frozen=True, slots=True)
class DailyBarWriteResult:
    inserted: int
    unchanged: int


class SqlDataBaselineRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_calendar(self, rows: tuple[TradingCalendarWrite, ...]) -> None:
        for row in rows:
            statement = insert(models.TradingCalendar).values(
                trade_date=row.trade_date,
                is_open=row.is_open,
                previous_open_date=row.previous_open_date,
                source=row.source,
                quality=row.quality.value,
                fetched_at=row.fetched_at,
                content_hash=row.content_hash,
            )
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=[models.TradingCalendar.trade_date],
                    set_={
                        "is_open": row.is_open,
                        "previous_open_date": row.previous_open_date,
                        "source": row.source,
                        "quality": row.quality.value,
                        "fetched_at": row.fetched_at,
                        "content_hash": row.content_hash,
                    },
                )
            )

    async def upsert_stock_master(
        self,
        records: tuple[StockMasterRecord, ...],
        *,
        observed_on: date,
    ) -> StockMasterWriteResult:
        if len({(record.code, record.exchange) for record in records}) != len(records):
            raise ValueError("stock master records must be unique")
        for record in records:
            statement = insert(models.Stock).values(
                code=record.code,
                exchange=record.exchange.value,
                board=record.board,
                name=record.name,
                listing_date=record.listing_date,
                status=record.status,
                source=record.source,
                quality=record.quality.value,
                fetched_at=record.fetched_at,
                content_hash=record.content_hash,
                last_seen_at=record.fetched_at,
            )
            await self.session.execute(
                statement.on_conflict_do_update(
                    constraint="uq_stocks_code_exchange",
                    set_={
                        "board": record.board,
                        "name": record.name,
                        "listing_date": record.listing_date,
                        "status": record.status,
                        "source": record.source,
                        "quality": record.quality.value,
                        "fetched_at": record.fetched_at,
                        "content_hash": record.content_hash,
                        "last_seen_at": record.fetched_at,
                    },
                )
            )
        stock_rows = (
            await self.session.execute(
                select(models.Stock).where(
                    tuple_(models.Stock.code, models.Stock.exchange).in_(
                        [
                            (record.code, record.exchange.value)
                            for record in records
                        ]
                    )
                )
            )
        ).scalars()
        stock_ids = {(row.code, row.exchange): row.id for row in stock_rows}
        created = 0
        closed = 0
        for record in records:
            if record.provider_industry is None:
                continue
            stock_id = stock_ids[(record.code, record.exchange.value)]
            active = await self.session.scalar(
                select(models.StockClassification).where(
                    models.StockClassification.stock_id == stock_id,
                    models.StockClassification.classification_type
                    == "provider_industry",
                    models.StockClassification.source == record.source,
                    models.StockClassification.valid_to.is_(None),
                )
            )
            if active is not None and active.label == record.provider_industry:
                active.fetched_at = record.fetched_at
                active.quality = record.quality.value
                continue
            payload = _classification_payload(record.provider_industry, observed_on)
            if active is not None and active.valid_from == observed_on:
                active.label = record.provider_industry
                active.fetched_at = record.fetched_at
                active.quality = record.quality.value
                active.content_hash = canonical_payload_hash(payload)
                continue
            if active is not None:
                active.valid_to = observed_on - timedelta(days=1)
                closed += 1
            self.session.add(
                models.StockClassification(
                    id=uuid.uuid4(),
                    stock_id=stock_id,
                    classification_type="provider_industry",
                    label=record.provider_industry,
                    domain=None,
                    sector_code=None,
                    is_primary=False,
                    source=record.source,
                    quality=record.quality.value,
                    valid_from=observed_on,
                    valid_to=None,
                    fetched_at=record.fetched_at,
                    content_hash=canonical_payload_hash(payload),
                )
            )
            created += 1
        await self.session.flush()
        return StockMasterWriteResult(len(records), created, closed)

    async def persist_provider_daily_series(
        self,
        stock_id: int,
        series: ProviderDailySeries,
    ) -> DailyBarWriteResult:
        inserted = 0
        unchanged = 0
        for bar in series.bars:
            payload = _daily_bar_payload(bar)
            content_hash = canonical_payload_hash(payload)
            existing_id = await self.session.scalar(
                select(models.DailyBar.id).where(
                    models.DailyBar.stock_id == stock_id,
                    models.DailyBar.trade_date == bar.trade_date,
                    models.DailyBar.adjustment == bar.adjustment,
                    models.DailyBar.source == bar.source,
                    models.DailyBar.content_hash == content_hash,
                )
            )
            if existing_id is not None:
                unchanged += 1
                continue
            self.session.add(
                models.DailyBar(
                    id=uuid.uuid4(),
                    stock_id=stock_id,
                    trade_date=bar.trade_date,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    amount=bar.amount,
                    adjustment=bar.adjustment,
                    source=bar.source,
                    quality=bar.quality.value,
                    fetched_at=bar.fetched_at,
                    content_hash=content_hash,
                )
            )
            inserted += 1
        await self.session.flush()
        return DailyBarWriteResult(inserted, unchanged)

    async def persist_snapshot(
        self,
        records: list[SnapshotInputRecord],
        *,
        cutoff: datetime,
        locked_at: datetime,
    ) -> tuple[models.DataSnapshot, FrozenSnapshot]:
        if len({(record.record_key, record.source) for record in records}) != len(records):
            raise ValueError("snapshot record keys must be unique per source")
        frozen = freeze_snapshot(
            [
                SnapshotRecord(
                    source=record.source,
                    as_of=record.as_of,
                    fetched_at=record.fetched_at,
                    quality=record.quality,
                    payload={"record_key": record.record_key, **record.payload},
                    published_at=record.published_at,
                )
                for record in records
            ],
            cutoff=cutoff,
            locked_at=locked_at,
        )
        existing = await self.session.scalar(
            select(models.DataSnapshot).where(
                models.DataSnapshot.content_hash == frozen.content_hash
            )
        )
        if existing is not None:
            return existing, frozen
        snapshot = models.DataSnapshot(
            id=uuid.uuid4(),
            as_of=cutoff,
            created_at=locked_at,
            quality=_snapshot_quality(records).value,
            content_hash=frozen.content_hash,
            locked_at=locked_at,
        )
        self.session.add(snapshot)
        await self.session.flush()
        for record in records:
            payload: dict[str, object] = dict(record.payload)
            self.session.add(
                models.DataSnapshotRecord(
                    id=uuid.uuid4(),
                    snapshot_id=snapshot.id,
                    record_key=record.record_key,
                    source=record.source,
                    as_of=record.as_of,
                    fetched_at=record.fetched_at,
                    published_at=(
                        record.published_at.isoformat()
                        if record.published_at is not None
                        else None
                    ),
                    adjustment=record.adjustment,
                    quality=record.quality.value,
                    payload=payload,
                    content_hash=canonical_payload_hash(payload),
                )
            )
        await self.session.flush()
        return snapshot, frozen

    async def persist_v9_inputs(
        self,
        snapshot_id: uuid.UUID,
        features: list[tuple[int, RuleFeatures]],
        state_input: MarketStateInput,
        *,
        created_at: datetime,
    ) -> None:
        if len({stock_id for stock_id, _ in features}) != len(features):
            raise ValueError("weekly feature stock ids must be unique")
        for stock_id, feature in features:
            payload = serialize_rule_features(feature)
            statement = insert(models.WeeklyFeature).values(
                id=uuid.uuid4(),
                snapshot_id=snapshot_id,
                stock_id=stock_id,
                feature_version=FEATURE_SCHEMA_VERSION,
                payload=payload,
                content_hash=canonical_payload_hash(payload),
                created_at=created_at,
            )
            await self.session.execute(
                statement.on_conflict_do_nothing(
                    constraint="uq_weekly_feature_version"
                )
            )
        state_payload = serialize_market_state_input(state_input)
        state_statement = insert(models.WeeklyStateInput).values(
            id=uuid.uuid4(),
            snapshot_id=snapshot_id,
            input_version=STATE_INPUT_SCHEMA_VERSION,
            payload=state_payload,
            content_hash=canonical_payload_hash(state_payload),
            created_at=created_at,
        )
        await self.session.execute(
            state_statement.on_conflict_do_nothing(
                constraint="uq_weekly_state_input_version"
            )
        )


def _snapshot_quality(records: list[SnapshotInputRecord]) -> DataQuality:
    qualities = {record.quality for record in records}
    if DataQuality.DEGRADED in qualities:
        return DataQuality.DEGRADED
    if DataQuality.SINGLE_SOURCE in qualities:
        return DataQuality.SINGLE_SOURCE
    return DataQuality.VERIFIED


def _classification_payload(label: str, valid_from: date) -> dict[str, object]:
    return {
        "classification_type": "provider_industry",
        "label": label,
        "valid_from": valid_from.isoformat(),
    }


def _daily_bar_payload(bar: NormalizedDailyBar) -> dict[str, object]:
    def decimal_text(value: Decimal | None) -> str | None:
        return None if value is None else format(value, "f")

    return {
        "stock_key": bar.stock_key,
        "trade_date": bar.trade_date.isoformat(),
        "open": decimal_text(bar.open),
        "high": decimal_text(bar.high),
        "low": decimal_text(bar.low),
        "close": decimal_text(bar.close),
        "volume": decimal_text(bar.volume),
        "amount": decimal_text(bar.amount),
        "adjustment": bar.adjustment,
        "source": bar.source,
    }
