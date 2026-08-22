import argparse
import asyncio
from datetime import UTC, date, datetime

import httpx
from pawe_api.data.exchange_calendar import (
    AnnualClosureManifest,
    build_verified_calendar_week,
    parse_annual_closure_manifest,
)
from pawe_api.data.repository import SqlDataBaselineRepository
from pawe_api.db.session import SessionFactory

SSE_2026_URL = "https://www.sse.com.cn/disclosure/dealinstruc/closed/"
SZSE_2026_URL = (
    "https://investor.szse.cn/disclosure/notice/general/t20251222_618087.html"
)


async def ingest(week_id: date, year: int, primary_url: str, backup_url: str) -> None:
    if week_id.weekday() != 0:
        raise ValueError("week_id must be a Monday")
    fetched_at = datetime.now(UTC)
    async with httpx.AsyncClient(follow_redirects=True, timeout=12.0) as client:
        primary, backup = await asyncio.gather(
            _fetch_manifest(client, "sse", primary_url, year, fetched_at),
            _fetch_manifest(client, "szse", backup_url, year, fetched_at),
        )
    rows = build_verified_calendar_week(week_id, primary, backup)
    async with SessionFactory() as session, session.begin():
        await SqlDataBaselineRepository(session).upsert_calendar(rows)
    quality = rows[0].quality.value
    open_dates = ",".join(row.trade_date.isoformat() for row in rows if row.is_open)
    print(f"week={week_id.isoformat()} quality={quality} open_dates={open_dates}")


async def _fetch_manifest(
    client: httpx.AsyncClient,
    source: str,
    source_url: str,
    year: int,
    fetched_at: datetime,
) -> AnnualClosureManifest:
    response = await client.get(source_url)
    response.raise_for_status()
    return parse_annual_closure_manifest(
        response.text,
        source=source,
        source_url=source_url,
        year=year,
        fetched_at=fetched_at,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest an A-share week from matching SSE and SZSE annual closure notices."
    )
    parser.add_argument("week_id", type=date.fromisoformat)
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--primary-url", default=SSE_2026_URL)
    parser.add_argument("--backup-url", default=SZSE_2026_URL)
    args = parser.parse_args()
    asyncio.run(ingest(args.week_id, args.year, args.primary_url, args.backup_url))


if __name__ == "__main__":
    main()
