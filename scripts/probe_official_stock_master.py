import argparse
import asyncio
from collections import Counter

import httpx
from pawe_api.data.providers import (
    BseStockMasterProvider,
    ProviderPolicy,
    SseStockMasterProvider,
    StockMasterBatch,
    SzseStockMasterProvider,
)


async def probe(exchanges: tuple[str, ...]) -> None:
    policy = ProviderPolicy(timeout_seconds=12, retry_count=1, min_interval_seconds=1)
    async with httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=5,
        trust_env=False,
    ) as client:
        providers: dict[
            str,
            SseStockMasterProvider | SzseStockMasterProvider | BseStockMasterProvider,
        ] = {
            "SSE": SseStockMasterProvider(client, policy=policy),
            "SZSE": SzseStockMasterProvider(client, policy=policy),
            "BSE": BseStockMasterProvider(client, policy=policy),
        }
        selected = [(exchange, providers[exchange]) for exchange in exchanges]
        results = await asyncio.gather(
            *(provider.fetch() for _, provider in selected),
            return_exceptions=True,
        )
    for (exchange, _provider), result in zip(selected, results, strict=True):
        if isinstance(result, StockMasterBatch):
            boards = Counter(record.board for record in result.records)
            statuses = Counter(record.status for record in result.records)
            print(
                f"exchange={exchange} status=ok expected={result.expected_total} "
                f"accepted={len(result.records)} rejected={len(result.warnings)} "
                f"boards={dict(sorted(boards.items()))} "
                f"stock_statuses={dict(sorted(statuses.items()))} "
                f"warnings={result.warnings[:5]}"
            )
        else:
            print(
                f"exchange={exchange} status=failed "
                f"reason={type(result).__name__}:{result}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read and validate exchange stock masters without database writes."
    )
    parser.add_argument(
        "exchange",
        nargs="*",
        choices=("SSE", "SZSE", "BSE"),
        default=("SSE", "SZSE", "BSE"),
    )
    args = parser.parse_args()
    asyncio.run(probe(tuple(args.exchange)))


if __name__ == "__main__":
    main()
