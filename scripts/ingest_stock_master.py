import argparse
import asyncio
from collections import Counter
from datetime import date

import httpx
from pawe_api.data.providers import (
    BseStockMasterProvider,
    EastmoneyStockMasterProvider,
    OfficialStockMasterProvider,
    ProviderPolicy,
    SseStockMasterProvider,
    SzseStockMasterProvider,
)
from pawe_api.data.repository import SqlDataBaselineRepository
from pawe_api.db.session import SessionFactory


async def ingest(observed_on: date, page_size: int, source: str) -> None:
    policy = ProviderPolicy(timeout_seconds=12, retry_count=2, min_interval_seconds=1)
    async with httpx.AsyncClient(follow_redirects=True, trust_env=False) as client:
        if source == "official":
            batch = await OfficialStockMasterProvider(
                SseStockMasterProvider(client, policy=policy),
                SzseStockMasterProvider(client, policy=policy),
                BseStockMasterProvider(client, policy=policy),
            ).fetch()
        else:
            batch = await EastmoneyStockMasterProvider(client, policy=policy).fetch(
                page_size=page_size
            )
    if batch.warnings or len(batch.records) != batch.expected_total:
        raise RuntimeError(
            "stock master coverage incomplete: "
            f"expected={batch.expected_total} accepted={len(batch.records)} "
            f"rejected={len(batch.warnings)}"
        )
    async with SessionFactory() as session, session.begin():
        result = await SqlDataBaselineRepository(session).upsert_stock_master(
            batch.records,
            observed_on=observed_on,
        )
    exchanges = Counter(record.exchange.value for record in batch.records)
    boards = Counter(record.board for record in batch.records)
    statuses = Counter(record.status for record in batch.records)
    print(
        f"observed_on={observed_on.isoformat()} expected={batch.expected_total} "
        f"route={source} "
        f"written={result.stocks_written} "
        f"classifications_created={result.classifications_created} "
        f"classifications_closed={result.classifications_closed}"
    )
    print(f"exchanges={dict(sorted(exchanges.items()))}")
    print(f"boards={dict(sorted(boards.items()))}")
    print(f"statuses={dict(sorted(statuses.items()))}")
    print(f"degradations={batch.degradations}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest the current A-share stock master without guessing rejected rows."
    )
    parser.add_argument(
        "--observed-on",
        type=date.fromisoformat,
        default=date.today(),
    )
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument(
        "--source",
        choices=("official", "eastmoney"),
        default="official",
    )
    args = parser.parse_args()
    asyncio.run(ingest(args.observed_on, args.page_size, args.source))


if __name__ == "__main__":
    main()
