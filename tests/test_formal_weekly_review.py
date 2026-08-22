from datetime import date, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from pawe_api.contracts import DataQuality
from pawe_api.evaluation import formal
from pawe_api.features.market_snapshot import DailyBriefObservation
from pawe_api.features.technical import FeatureCalculationError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_review_industry_peers_skip_unavailable_close_data(monkeypatch) -> None:
    open_dates = {date(2026, 8, day) for day in range(10, 15)}

    async def fake_observation(
        session: AsyncSession,
        stock: SimpleNamespace,
        *,
        as_of: date,
        snapshot_cutoff: datetime,
    ) -> DailyBriefObservation:
        del session, as_of, snapshot_cutoff
        code = cast(str, stock.code)
        if code == "000002":
            raise FeatureCalculationError("source unavailable")
        return DailyBriefObservation(
            quality=DataQuality.SINGLE_SOURCE,
            payload={
                "source_bars": {
                    "tencent": [
                        {
                            "trade_date": trade_date.isoformat(),
                            "open": 10,
                            "high": 11,
                            "low": 9,
                            "close": 10.5,
                        }
                        for trade_date in sorted(open_dates)
                    ]
                }
            },
        )

    monkeypatch.setattr(formal, "build_stored_daily_brief_observation", fake_observation)
    stocks = [SimpleNamespace(code="000001"), SimpleNamespace(code="000002")]

    bars, qualities, unavailable = await formal._load_review_bars(
        cast(AsyncSession, object()),
        cast(list, stocks),
        as_of=date(2026, 8, 14),
        snapshot_cutoff=datetime.fromisoformat("2026-08-18T10:00:00+08:00"),
        open_dates=open_dates,
    )

    assert list(bars) == ["000001"]
    assert len(bars["000001"]) == 5
    assert qualities == {"000001": DataQuality.SINGLE_SOURCE}
    assert unavailable == ["000002"]
