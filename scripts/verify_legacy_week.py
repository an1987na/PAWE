import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import date

import httpx
from pawe_api.data.providers import TencentDailyProvider
from pawe_api.db.session import SessionFactory
from pawe_api.experiments.batch_verification import verify_selection_week


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recalculate one staged legacy week.")
    parser.add_argument("selection_date", type=date.fromisoformat)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


async def verify(selection_date: date, *, force: bool) -> dict[str, object]:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        provider = TencentDailyProvider(client)
        async with SessionFactory() as session:
            result = await verify_selection_week(
                session,
                provider,
                selection_date,
                force=force,
            )
    return asdict(result)


def main() -> None:
    args = parse_args()
    print(
        json.dumps(
            asyncio.run(verify(args.selection_date, force=args.force)),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
