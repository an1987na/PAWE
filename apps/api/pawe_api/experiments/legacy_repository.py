import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pawe_api.db.models import (
    LegacyDocumentStaging,
    LegacyItemStaging,
    LegacyMigrationBatch,
)
from pawe_api.experiments.legacy import LegacyBatch, LegacyDocument, LegacyItem


@dataclass(frozen=True, slots=True)
class LegacyStagingResult:
    batch_id: uuid.UUID
    created: bool
    document_count: int
    item_count: int


async def persist_legacy_batch(
    session: AsyncSession,
    batch: LegacyBatch,
    *,
    source_label: str = "pick_a_weekly",
) -> LegacyStagingResult:
    existing = await session.scalar(
        select(LegacyMigrationBatch).where(
            LegacyMigrationBatch.manifest_hash == batch.manifest_hash
        )
    )
    if existing is not None:
        document_count, item_count = await _batch_counts(session, existing.id)
        return LegacyStagingResult(existing.id, False, document_count, item_count)

    batch_id = uuid.uuid4()
    session.add(
        LegacyMigrationBatch(
            id=batch_id,
            source_label=source_label,
            manifest_hash=batch.manifest_hash,
            source_file_count=len(batch.documents),
            status="staged_unverified",
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()
    item_count = 0
    for document in batch.documents:
        document_id = await _persist_document(session, batch_id, document)
        for item in document.items:
            session.add(_item_row(document_id, item))
            item_count += 1
        await session.flush()
    await session.commit()
    return LegacyStagingResult(batch_id, True, len(batch.documents), item_count)


async def _persist_document(
    session: AsyncSession,
    batch_id: uuid.UUID,
    document: LegacyDocument,
) -> uuid.UUID:
    document_id = uuid.uuid4()
    session.add(
        LegacyDocumentStaging(
            id=document_id,
            batch_id=batch_id,
            source_ref=document.source.relative_path,
            source_hash=document.source.sha256,
            document_type=document.document_type.value,
            document_date=document.document_date,
            rule_version=document.rule_version,
            linked_source_ref=document.linked_source_ref,
            parse_quality=document.parse_quality.value,
            verification_status=document.verification_status,
            warnings=list(document.warnings),
        )
    )
    await session.flush()
    return document_id


def _item_row(document_id: uuid.UUID, item: LegacyItem) -> LegacyItemStaging:
    return LegacyItemStaging(
        id=uuid.uuid4(),
        document_id=document_id,
        bucket=item.bucket.value,
        stock_code=item.stock_code,
        stock_name=item.stock_name,
        direction=item.direction,
        rank=item.rank,
        baseline_price=_decimal(item.baseline_price),
        target_return=_decimal(item.target_return),
        week_high_return=_decimal(item.week_high_return),
        close_return=_decimal(item.close_return),
        max_drawdown=_decimal(item.max_drawdown),
        verification_status="legacy_unverified",
    )


async def _batch_counts(session: AsyncSession, batch_id: uuid.UUID) -> tuple[int, int]:
    document_count = await session.scalar(
        select(func.count())
        .select_from(LegacyDocumentStaging)
        .where(LegacyDocumentStaging.batch_id == batch_id)
    )
    item_count = await session.scalar(
        select(func.count())
        .select_from(LegacyItemStaging)
        .join(
            LegacyDocumentStaging,
            LegacyDocumentStaging.id == LegacyItemStaging.document_id,
        )
        .where(LegacyDocumentStaging.batch_id == batch_id)
    )
    return int(document_count or 0), int(item_count or 0)


def _decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))
