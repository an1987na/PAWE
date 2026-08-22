import argparse
import asyncio
from datetime import UTC, date, datetime, time

from pawe_api.db import models
from pawe_api.db.session import SessionFactory
from pawe_api.features.market_data import calculate_stored_technical_features
from sqlalchemy import select


async def report(code: str, as_of: date, cutoff: datetime, source: str) -> None:
    async with SessionFactory() as session:
        stock = await session.scalar(
            select(models.Stock).where(models.Stock.code == code)
        )
        if stock is None:
            raise ValueError(f"unknown stock code: {code}")
        features = await calculate_stored_technical_features(
            session,
            stock_id=stock.id,
            as_of=as_of,
            snapshot_cutoff=cutoff,
            source=source,
        )
    print(
        f"code={code} as_of={features.as_of.isoformat()} "
        f"return_5d={features.return_5d:.6f} "
        f"return_20d={features.return_20d:.6f} "
        f"return_60d={features.return_60d:.6f} "
        f"avg_amount_20d={features.avg_amount_20d:.2f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build technical features from versioned bars available by a cutoff."
    )
    parser.add_argument("code")
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--cutoff", type=datetime.fromisoformat)
    parser.add_argument("--source", default="eastmoney")
    args = parser.parse_args()
    cutoff = args.cutoff or datetime.combine(args.as_of, time.max, tzinfo=UTC)
    if cutoff.tzinfo is None:
        parser.error("--cutoff must include a timezone")
    asyncio.run(report(args.code, args.as_of, cutoff, args.source))


if __name__ == "__main__":
    main()
