import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import date

import httpx
from pawe_api.data.providers import TencentDailyProvider
from pawe_api.db.models import LegacyDocumentStaging
from pawe_api.db.session import SessionFactory
from pawe_api.experiments.batch_verification import verify_selection_week
from sqlalchemy import select


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recalculate all eligible staged legacy weeks with resumable progress."
    )
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


async def selection_dates(
    *, start: date | None, end: date | None, limit: int | None
) -> tuple[date, ...]:
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    statement = (
        select(LegacyDocumentStaging.document_date)
        .where(
            LegacyDocumentStaging.document_type == "weekly_selection",
            LegacyDocumentStaging.document_date.is_not(None),
        )
        .distinct()
        .order_by(LegacyDocumentStaging.document_date)
    )
    if start is not None:
        statement = statement.where(LegacyDocumentStaging.document_date >= start)
    if end is not None:
        statement = statement.where(LegacyDocumentStaging.document_date <= end)
    if limit is not None:
        statement = statement.limit(limit)
    async with SessionFactory() as session:
        values = (await session.scalars(statement)).all()
    return tuple(value for value in values if value is not None)


async def verify_batch(args: argparse.Namespace) -> dict[str, int]:
    dates = await selection_dates(start=args.start, end=args.end, limit=args.limit)
    totals = {
        "week_count": len(dates),
        "eligible_count": 0,
        "processed_count": 0,
        "skipped_count": 0,
        "matched_count": 0,
        "baseline_drift_count": 0,
        "conflicted_count": 0,
        "insufficient_count": 0,
    }
    async with httpx.AsyncClient(follow_redirects=True) as client:
        provider = TencentDailyProvider(client)
        for index, selection_date in enumerate(dates, start=1):
            async with SessionFactory() as session:
                result = await verify_selection_week(
                    session,
                    provider,
                    selection_date,
                    force=args.force,
                )
            payload = asdict(result)
            print(
                json.dumps(
                    {"progress": f"{index}/{len(dates)}", **payload},
                    ensure_ascii=False,
                    default=str,
                ),
                flush=True,
            )
            for key in totals:
                if key != "week_count":
                    totals[key] += int(payload[key])
    return totals


def main() -> None:
    args = parse_args()
    if args.start is not None and args.end is not None and args.start > args.end:
        raise SystemExit("--start cannot be later than --end")
    totals = asyncio.run(verify_batch(args))
    print(json.dumps({"summary": totals}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
