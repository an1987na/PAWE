import argparse
import asyncio
from datetime import UTC, date, datetime, timedelta

import httpx
from pawe_api.data.providers import (
    DailyProviderError,
    EastmoneyDailyProvider,
    ProviderPolicy,
    TencentDailyProvider,
)
from pawe_api.db.session import SessionFactory
from pawe_api.experiments.historical_week import HistoricalWeekReplayApplication


async def replay(week_id: date) -> None:
    async with httpx.AsyncClient(follow_redirects=True, trust_env=False) as client:
        policy = ProviderPolicy(
            timeout_seconds=10,
            retry_count=2,
            min_interval_seconds=0,
        )
        try:
            benchmark = await TencentDailyProvider(client, policy=policy).fetch(
                "sh000300", week_id, week_id + timedelta(days=4)
            )
        except DailyProviderError:
            benchmark = await EastmoneyDailyProvider(client, policy=policy).fetch(
                "sh000300", week_id, week_id + timedelta(days=4)
            )
    if not benchmark.bars:
        raise RuntimeError("CSI300 benchmark bars are unavailable")
    benchmark_return = float(benchmark.bars[-1].close / benchmark.bars[0].open - 1)
    result = await HistoricalWeekReplayApplication(SessionFactory).run(
        week_id,
        actual_run_at=datetime.now(UTC),
        benchmark_return=benchmark_return,
    )
    print(
        f"week={result.week_id.isoformat()} status={result.status} "
        f"selected={','.join(result.selected_codes)} briefs={len(result.daily_briefs)}"
    )
    print(result.review.summary)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a point-in-time historical week research replay."
    )
    parser.add_argument("week_id", type=date.fromisoformat)
    arguments = parser.parse_args()
    asyncio.run(replay(arguments.week_id))


if __name__ == "__main__":
    main()
