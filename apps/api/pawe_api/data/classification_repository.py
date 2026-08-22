import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pawe_api.contracts import DataQuality
from pawe_api.data.classification import (
    PRIMARY_CLASSIFICATION_TYPE,
    PRIMARY_SOURCE,
    ClassificationRecord,
    PrimaryClassificationResult,
    PrimaryClassificationStatus,
)
from pawe_api.db import models
from pawe_api.rules.models import Domain


@dataclass(frozen=True, slots=True)
class ClassificationWriteResult:
    created: int
    updated: int
    closed: int
    unknown_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PrimaryClassificationWriteResult:
    created: int
    unchanged: int
    closed: int
    missing: int
    conflicted: int


@dataclass(frozen=True, slots=True)
class StoredPrimaryClassification:
    stock_id: int
    stock_code: str
    domain: Domain
    sector_code: str
    quality: DataQuality
    valid_from: date
    valid_to: date | None
    published_at: date
    fetched_at: datetime
    content_hash: str


class SqlClassificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_evidence_batch(
        self,
        records: tuple[ClassificationRecord, ...],
        *,
        complete_source_snapshot: bool,
    ) -> ClassificationWriteResult:
        if not records:
            raise ValueError("classification batch cannot be empty")
        batch_keys = {
            (record.classification_type, record.source, record.valid_from)
            for record in records
        }
        if len(batch_keys) != 1:
            raise ValueError("classification batch must contain one source version")
        codes = [record.stock_code for record in records]
        if len(codes) != len(set(codes)):
            raise ValueError("classification batch stock codes must be unique")
        stock_rows = (
            await self.session.execute(select(models.Stock).where(models.Stock.code.in_(codes)))
        ).scalars()
        stock_ids = {stock.code: stock.id for stock in stock_rows}
        unknown_codes = tuple(sorted(set(codes) - set(stock_ids)))
        classification_type, source, valid_from = next(iter(batch_keys))
        created = 0
        updated = 0
        closed = 0
        known_codes = set(stock_ids)

        active_rows = (
            await self.session.execute(
                select(models.StockClassification, models.Stock.code)
                .join(models.Stock, models.Stock.id == models.StockClassification.stock_id)
                .where(
                    models.StockClassification.classification_type == classification_type,
                    models.StockClassification.source == source,
                    models.StockClassification.valid_to.is_(None),
                )
            )
        ).all()
        active_by_code = {code: row for row, code in active_rows}
        if complete_source_snapshot:
            for code, row in active_by_code.items():
                if code not in known_codes and row.valid_from < valid_from:
                    row.valid_to = valid_from - timedelta(days=1)
                    closed += 1

        for record in records:
            stock_id = stock_ids.get(record.stock_code)
            if stock_id is None:
                continue
            current = active_by_code.get(record.stock_code)
            if current is not None and current.valid_from == record.valid_from:
                current.label = record.label
                current.domain = record.domain.value if record.domain else None
                current.sector_code = record.sector_code
                current.quality = record.quality.value
                current.valid_to = record.valid_to
                current.published_at = record.published_at
                current.evidence_url = record.evidence_url
                current.fetched_at = record.fetched_at
                current.content_hash = record.content_hash
                updated += 1
                continue
            if current is not None:
                if current.valid_from > record.valid_from:
                    raise ValueError("cannot insert classification before the active version")
                current.valid_to = record.valid_from - timedelta(days=1)
                closed += 1
            self.session.add(
                models.StockClassification(
                    id=uuid.uuid4(),
                    stock_id=stock_id,
                    classification_type=record.classification_type,
                    label=record.label,
                    domain=record.domain.value if record.domain else None,
                    sector_code=record.sector_code,
                    is_primary=False,
                    source=record.source,
                    quality=record.quality.value,
                    valid_from=record.valid_from,
                    valid_to=record.valid_to,
                    published_at=record.published_at,
                    evidence_url=record.evidence_url,
                    fetched_at=record.fetched_at,
                    content_hash=record.content_hash,
                )
            )
            created += 1
        await self.session.flush()
        return ClassificationWriteResult(
            created=created,
            updated=updated,
            closed=closed,
            unknown_codes=unknown_codes,
        )

    async def load_evidence_as_of(
        self,
        *,
        as_of: date,
    ) -> dict[str, tuple[ClassificationRecord, ...]]:
        rows = (
            await self.session.execute(
                select(models.StockClassification, models.Stock.code)
                .join(models.Stock, models.Stock.id == models.StockClassification.stock_id)
                .where(
                    models.StockClassification.classification_type
                    != PRIMARY_CLASSIFICATION_TYPE,
                    models.StockClassification.valid_from <= as_of,
                    (
                        models.StockClassification.valid_to.is_(None)
                        | (models.StockClassification.valid_to >= as_of)
                    ),
                )
            )
        ).all()
        grouped: dict[str, list[ClassificationRecord]] = {}
        for row, code in rows:
            grouped.setdefault(code, []).append(
                ClassificationRecord(
                    stock_code=code,
                    classification_type=row.classification_type,
                    label=row.label,
                    domain=Domain(row.domain) if row.domain else None,
                    sector_code=row.sector_code,
                    source=row.source,
                    quality=DataQuality(row.quality),
                    valid_from=row.valid_from,
                    valid_to=row.valid_to,
                    published_at=row.published_at,
                    evidence_url=row.evidence_url,
                    fetched_at=row.fetched_at,
                    content_hash=row.content_hash,
                )
            )
        return {code: tuple(records) for code, records in grouped.items()}

    async def replace_primary_classifications(
        self,
        results: tuple[PrimaryClassificationResult, ...],
        *,
        as_of: date,
    ) -> PrimaryClassificationWriteResult:
        codes = [result.stock_code for result in results]
        if len(codes) != len(set(codes)):
            raise ValueError("primary classification results must be unique")
        stock_rows = (
            await self.session.execute(select(models.Stock).where(models.Stock.code.in_(codes)))
        ).scalars()
        stock_ids = {stock.code: stock.id for stock in stock_rows}
        if set(codes) != set(stock_ids):
            raise ValueError("primary classification contains unknown stock codes")
        active_rows = (
            await self.session.execute(
                select(models.StockClassification, models.Stock.code)
                .join(models.Stock, models.Stock.id == models.StockClassification.stock_id)
                .where(
                    models.StockClassification.classification_type
                    == PRIMARY_CLASSIFICATION_TYPE,
                    models.StockClassification.source == PRIMARY_SOURCE,
                    models.StockClassification.valid_to.is_(None),
                )
            )
        ).all()
        active_by_code = {code: row for row, code in active_rows}
        created = 0
        unchanged = 0
        closed = 0
        missing = 0
        conflicted = 0
        for result in results:
            current = active_by_code.get(result.stock_code)
            if result.status is PrimaryClassificationStatus.MISSING:
                missing += 1
            elif result.status is PrimaryClassificationStatus.CONFLICTED:
                conflicted += 1
            if result.primary is None:
                if current is not None and current.valid_from <= as_of:
                    current.valid_to = as_of - timedelta(days=1)
                    closed += 1
                continue
            primary = result.primary
            if (
                current is not None
                and current.domain == primary.domain.value
                and current.sector_code == primary.sector_code
                and current.content_hash == primary.content_hash
            ):
                current.published_at = primary.published_at
                current.fetched_at = min(current.fetched_at, primary.fetched_at)
                unchanged += 1
                continue
            if current is not None:
                if primary.valid_from <= current.valid_from:
                    raise ValueError("cannot replace primary classification retroactively")
                current.valid_to = primary.valid_from - timedelta(days=1)
                closed += 1
            self.session.add(
                models.StockClassification(
                    id=uuid.uuid4(),
                    stock_id=stock_ids[result.stock_code],
                    classification_type=PRIMARY_CLASSIFICATION_TYPE,
                    label=primary.label,
                    domain=primary.domain.value,
                    sector_code=primary.sector_code,
                    is_primary=True,
                    source=PRIMARY_SOURCE,
                    quality=primary.quality.value,
                    valid_from=primary.valid_from,
                    valid_to=None,
                    published_at=primary.published_at,
                    evidence_url=None,
                    fetched_at=primary.fetched_at,
                    content_hash=primary.content_hash,
                )
            )
            created += 1
        await self.session.flush()
        return PrimaryClassificationWriteResult(
            created=created,
            unchanged=unchanged,
            closed=closed,
            missing=missing,
            conflicted=conflicted,
        )

    async def load_primary_as_of(
        self,
        *,
        available_on: date,
        published_by: date,
        fetched_by: datetime,
        stock_ids: tuple[int, ...] | None = None,
    ) -> dict[str, StoredPrimaryClassification]:
        statement = (
            select(models.StockClassification, models.Stock.code)
            .join(models.Stock, models.Stock.id == models.StockClassification.stock_id)
            .where(
                models.StockClassification.classification_type
                == PRIMARY_CLASSIFICATION_TYPE,
                models.StockClassification.source == PRIMARY_SOURCE,
                models.StockClassification.is_primary.is_(True),
                models.StockClassification.valid_from <= available_on,
                (
                    models.StockClassification.valid_to.is_(None)
                    | (models.StockClassification.valid_to >= available_on)
                ),
                models.StockClassification.published_at.is_not(None),
                models.StockClassification.published_at <= published_by,
                models.StockClassification.fetched_at <= fetched_by,
                models.StockClassification.quality.in_(
                    [DataQuality.VERIFIED.value, DataQuality.SINGLE_SOURCE.value]
                ),
            )
        )
        if stock_ids is not None:
            if not stock_ids:
                return {}
            statement = statement.where(models.StockClassification.stock_id.in_(stock_ids))
        rows = (await self.session.execute(statement)).all()
        loaded: dict[str, StoredPrimaryClassification] = {}
        for row, code in rows:
            if code in loaded:
                raise ValueError(f"overlapping primary classifications: {code}")
            if row.domain is None or row.sector_code is None or row.published_at is None:
                raise ValueError(f"invalid primary classification shape: {code}")
            loaded[code] = StoredPrimaryClassification(
                stock_id=row.stock_id,
                stock_code=code,
                domain=Domain(row.domain),
                sector_code=row.sector_code,
                quality=DataQuality(row.quality),
                valid_from=row.valid_from,
                valid_to=row.valid_to,
                published_at=row.published_at,
                fetched_at=row.fetched_at,
                content_hash=row.content_hash,
            )
        return loaded

    async def load_primary_information_as_of(
        self,
        *,
        available_on: date,
        published_by: date,
        retrieved_by: datetime,
        stock_ids: tuple[int, ...] | None = None,
    ) -> dict[str, StoredPrimaryClassification]:
        """Load classifications whose information date was visible in a replay.

        The real fetch timestamp is bounded by ``retrieved_by`` but is deliberately
        not backdated to the simulated decision time.
        """
        return await self.load_primary_as_of(
            available_on=available_on,
            published_by=published_by,
            fetched_by=retrieved_by,
            stock_ids=stock_ids,
        )
