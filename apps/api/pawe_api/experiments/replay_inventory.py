from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pawe_api.db.models import LegacyDocumentStaging, LegacyItemStaging
from pawe_api.experiments.legacy_attribution import replay_arm


@dataclass(frozen=True, slots=True)
class LegacyOutcomeSet:
    review_date: date
    arm: str
    source_ref: str
    published_item_count: int
    ready_item_count: int
    status: str


async def build_legacy_outcome_inventory(
    session: AsyncSession,
) -> tuple[LegacyOutcomeSet, ...]:
    rows = (
        await session.execute(
            select(LegacyDocumentStaging, LegacyItemStaging)
            .join(
                LegacyItemStaging,
                LegacyItemStaging.document_id == LegacyDocumentStaging.id,
            )
            .where(
                LegacyDocumentStaging.document_type == "weekly_review",
                LegacyDocumentStaging.document_date.is_not(None),
                LegacyItemStaging.bucket == "main",
            )
            .order_by(LegacyDocumentStaging.document_date, LegacyDocumentStaging.source_ref)
        )
    ).all()
    grouped: dict[str, tuple[LegacyDocumentStaging, list[LegacyItemStaging]]] = {}
    for document, item in rows:
        entry = grouped.setdefault(document.source_ref, (document, []))
        entry[1].append(item)

    inventory: list[LegacyOutcomeSet] = []
    for source_ref, (document, items) in grouped.items():
        assert document.document_date is not None
        ready_count = sum(
            item.replay_eligibility == "outcome_ready_single_source" for item in items
        )
        inventory.append(
            LegacyOutcomeSet(
                review_date=document.document_date,
                arm=replay_arm(source_ref),
                source_ref=source_ref,
                published_item_count=len(items),
                ready_item_count=ready_count,
                status=outcome_set_status(len(items), ready_count),
            )
        )
    return tuple(inventory)


def outcome_set_status(published_item_count: int, ready_item_count: int) -> str:
    if published_item_count <= 0:
        return "excluded_empty_outcome"
    if ready_item_count < 0 or ready_item_count > published_item_count:
        raise ValueError("ready item count must be within published item count")
    if ready_item_count == published_item_count:
        return "research_ready_single_source"
    return "excluded_incomplete_outcome"
