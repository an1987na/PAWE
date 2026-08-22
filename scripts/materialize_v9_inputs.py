import argparse
import asyncio
import uuid
from collections import Counter
from datetime import UTC, datetime

from pawe_api.contracts import DataQuality, MarketState
from pawe_api.data.repository import SqlDataBaselineRepository
from pawe_api.data.snapshot import FrozenSnapshot
from pawe_api.db import models
from pawe_api.db.session import SessionFactory
from pawe_api.features.weekly import build_degraded_market_state_input, build_rule_features
from pawe_api.rules.engine import run_v9_rules
from sqlalchemy import select


async def materialize(snapshot_id: uuid.UUID, *, persist: bool) -> None:
    async with SessionFactory() as session:
        snapshot = await session.get(models.DataSnapshot, snapshot_id)
        if snapshot is None:
            raise RuntimeError("snapshot does not exist")
        rows = list(
            (
                await session.execute(
                    select(models.DataSnapshotRecord, models.Stock)
                    .join(
                        models.Stock,
                        models.DataSnapshotRecord.record_key
                        == "classified_market:" + models.Stock.code,
                    )
                    .where(models.DataSnapshotRecord.snapshot_id == snapshot_id)
                    .order_by(models.Stock.code)
                )
            ).all()
        )
        previous_state_value = await session.scalar(
            select(models.Week.market_state)
            .where(models.Week.week_id < snapshot.as_of.date())
            .order_by(models.Week.week_id.desc())
            .limit(1)
        )
    feature_rows = build_rule_features(
        [
            (
                stock.id,
                stock.code,
                stock.name,
                stock.board,
                stock.status,
                stock.listing_date,
                record.payload,
                DataQuality(record.quality),
            )
            for record, stock in rows
        ]
    )
    state_input = build_degraded_market_state_input(
        MarketState(previous_state_value) if previous_state_value else MarketState.NORMAL
    )
    frozen = FrozenSnapshot(
        cutoff=snapshot.as_of,
        locked_at=snapshot.locked_at,
        content_hash=snapshot.content_hash,
        records=(),
    )
    result = run_v9_rules(
        snapshot=frozen,
        features=[feature for _, feature in feature_rows],
        market_state_input=state_input,
        candidate_overheat_ratio=(
            sum(feature.return_20d > 0.40 for _, feature in feature_rows) / len(feature_rows)
            if feature_rows
            else 0.0
        ),
    )
    buckets = Counter(candidate.bucket.value for candidate in result.candidates)
    print(
        f"snapshot_id={snapshot_id} features={len(feature_rows)} "
        f"buckets={dict(sorted(buckets.items()))} state={result.market_state.value} "
        f"flags={list(result.flags)}"
    )
    for rank, candidate in enumerate(result.baseline.items, start=1):
        print(
            f"rank={rank} code={candidate.features.stock_code} "
            f"name={candidate.features.stock_name} score={candidate.rule_score:.4f} "
            f"sector={candidate.features.primary_sector} "
            f"quality={candidate.features.data_quality.value}"
        )
    if not persist:
        return
    async with SessionFactory() as session, session.begin():
        await SqlDataBaselineRepository(session).persist_v9_inputs(
            snapshot_id,
            feature_rows,
            state_input,
            created_at=datetime.now(UTC),
        )
    print("persisted=true")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build conservative V9 inputs from a snapshot.")
    parser.add_argument("--snapshot-id", type=uuid.UUID, required=True)
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args()
    asyncio.run(materialize(args.snapshot_id, persist=args.persist))


if __name__ == "__main__":
    main()
