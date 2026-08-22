import argparse
import asyncio
from collections import Counter
from datetime import UTC, date, datetime, time

from pawe_api.data.calendar import SHANGHAI
from pawe_api.data.classification_repository import SqlClassificationRepository
from pawe_api.data.repository import SnapshotInputRecord, SqlDataBaselineRepository
from pawe_api.db import models
from pawe_api.db.session import SessionFactory
from pawe_api.features.market_snapshot import build_stored_technical_observation
from pawe_api.features.sector_market import build_classified_market_observations
from pawe_api.features.technical import FeatureCalculationError
from sqlalchemy import select


async def materialize(
    *,
    as_of: date,
    decision_cutoff: datetime,
    fetched_by: datetime,
    available_on: date,
    codes: tuple[str, ...],
    limit: int | None,
    persist: bool,
) -> None:
    all_stocks = await _load_stocks(codes)
    async with SessionFactory() as session:
        classifications = await SqlClassificationRepository(session).load_primary_as_of(
            available_on=available_on,
            published_by=as_of,
            fetched_by=fetched_by,
            stock_ids=tuple(stock.id for stock in all_stocks),
        )
    classified_stocks = [stock for stock in all_stocks if stock.code in classifications]
    stocks = classified_stocks[:limit] if limit is not None else classified_stocks
    excluded_classification_count = len(all_stocks) - len(classified_stocks)
    observations = []
    failures: list[str] = []
    async with SessionFactory() as session:
        for stock in stocks:
            try:
                observations.append(
                    await build_stored_technical_observation(
                        session,
                        stock,
                        as_of=as_of,
                        snapshot_cutoff=fetched_by,
                    )
                )
            except (FeatureCalculationError, ValueError) as exc:
                failures.append(f"{stock.code}:{type(exc).__name__}:{exc}")
    qualities = Counter(item.quality.value for item in observations)
    classified_observations = build_classified_market_observations(
        observations,
        classifications,
    )
    print(
        f"formal_universe={len(classified_stocks)} "
        f"classification_excluded={excluded_classification_count} "
        f"stocks={len(stocks)} ready={len(observations)} failed={len(failures)} "
        f"qualities={dict(sorted(qualities.items()))}"
    )
    for observation in observations[:5]:
        features = observation.features
        print(
            f"code={observation.stock_code} as_of={features.as_of.isoformat()} "
            f"return_20d={features.return_20d:.6f} "
            f"avg_amount_20d={features.avg_amount_20d:.2f}"
        )
    if failures:
        print("failures=" + ";".join(failures[:20]))
    if not persist:
        return
    if codes or limit is not None:
        raise RuntimeError("only a complete all-stock run may persist a technical snapshot")
    if failures or len(observations) != len(stocks):
        raise RuntimeError("classified technical snapshot coverage is incomplete")
    locked_at = datetime.now(UTC)
    records = [
        SnapshotInputRecord(
            record_key=f"classified_market:{item.technical.stock_code}",
            source="classified_market_v1",
            as_of=datetime.combine(item.technical.as_of, time(15, 0), tzinfo=SHANGHAI),
            fetched_at=max(
                item.technical.fetched_at,
                item.classification.fetched_at,
            ),
            published_at=item.classification.published_at,
            quality=item.technical.quality,
            payload=item.snapshot_payload(),
            adjustment="qfq",
        )
        for item in classified_observations
    ]
    async with SessionFactory() as session, session.begin():
        snapshot, _ = await SqlDataBaselineRepository(session).persist_snapshot(
            records,
            cutoff=decision_cutoff,
            locked_at=locked_at,
        )
    print(
        f"snapshot_id={snapshot.id} records={len(records)} "
        f"quality={snapshot.quality} content_hash={snapshot.content_hash}"
    )


async def _load_stocks(
    codes: tuple[str, ...],
) -> list[models.Stock]:
    statement = (
        select(models.Stock)
        .where(
            models.Stock.status == "active",
            models.Stock.exchange.in_(("SSE", "SZSE")),
            models.Stock.board != "star",
        )
        .order_by(models.Stock.code)
    )
    if codes:
        statement = statement.where(models.Stock.code.in_(codes))
    async with SessionFactory() as session:
        return list((await session.scalars(statement)).all())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate or persist an atomic technical market snapshot."
    )
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--decision-cutoff",
        type=datetime.fromisoformat,
        help="last completed trading-session cutoff stored on the frozen snapshot",
    )
    parser.add_argument(
        "--cutoff",
        type=datetime.fromisoformat,
        help="deprecated alias for --decision-cutoff",
    )
    parser.add_argument(
        "--fetched-by",
        type=datetime.fromisoformat,
        help="latest fetch time visible to the snapshot; defaults to decision cutoff",
    )
    parser.add_argument(
        "--available-on",
        type=date.fromisoformat,
        help="classification effective date; defaults to --as-of",
    )
    parser.add_argument("--code", action="append", default=[])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args()
    decision_cutoff = args.decision_cutoff or args.cutoff
    if decision_cutoff is None:
        parser.error("--decision-cutoff is required")
    if args.decision_cutoff is not None and args.cutoff is not None:
        parser.error("use only --decision-cutoff; --cutoff is a deprecated alias")
    fetched_by = args.fetched_by or decision_cutoff
    if decision_cutoff.tzinfo is None or decision_cutoff.utcoffset() is None:
        parser.error("--decision-cutoff must include a timezone")
    if fetched_by.tzinfo is None or fetched_by.utcoffset() is None:
        parser.error("--fetched-by must include a timezone")
    if fetched_by < decision_cutoff:
        parser.error("--fetched-by cannot precede --decision-cutoff")
    if args.limit <= 0:
        parser.error("--limit must be positive")
    asyncio.run(
        materialize(
            as_of=args.as_of,
            decision_cutoff=decision_cutoff,
            fetched_by=fetched_by,
            available_on=args.available_on or args.as_of,
            codes=tuple(args.code),
            limit=None if args.all else args.limit,
            persist=args.persist,
        )
    )


if __name__ == "__main__":
    main()
