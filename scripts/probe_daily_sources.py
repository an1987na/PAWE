import argparse
import asyncio
import json
from datetime import date

import httpx
from pawe_api.data.providers import (
    DailySeriesGateway,
    EastmoneyDailyProvider,
    TencentDailyProvider,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe PAWE public daily-bar providers.")
    parser.add_argument("stock_keys", nargs="+", help="Symbols such as sz300383 or sh600519")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    return parser.parse_args()


async def probe(stock_keys: list[str], start: date, end: date) -> list[dict[str, object]]:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        gateway = DailySeriesGateway(
            TencentDailyProvider(client),
            EastmoneyDailyProvider(client),
        )
        output: list[dict[str, object]] = []
        for stock_key in stock_keys:
            result = await gateway.fetch(stock_key, start, end)
            output.append(
                {
                    "stock_key": stock_key,
                    "quality": result.quality.value,
                    "bar_count": len(result.bars),
                    "sources": result.sources,
                    "warnings": result.warnings,
                }
            )
        return output


def main() -> None:
    args = parse_args()
    result = asyncio.run(probe(args.stock_keys, args.start, args.end))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
