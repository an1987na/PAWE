import uuid
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pawe_worker.main as worker
import pytest
from pawe_api.contracts import ManualOutputJobRequest
from pawe_api.jobs.repository import SqlJobApplication

WEEK = date(2026, 9, 7)


class Session:
    def __init__(self, *values):
        self.values = iter(values)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def begin(self):
        return self

    async def scalar(self, query):
        return next(self.values)

    async def scalars(self, query):
        return next(self.values)

    def add(self, value):
        self.added = value

    async def flush(self):
        pass


@pytest.mark.asyncio
async def test_existing_today_does_not_short_circuit_week_catchup():
    session = Session(None, uuid.uuid4(), None, None)
    response = await SqlJobApplication(session).enqueue_output_job(
        ManualOutputJobRequest(
            job_type="daily_brief",
            week_id=WEEK,
            trade_date=date(2026, 9, 10),
            catch_up_week=True,
            idempotency_key="catchup-current-week",
        ),
        uuid.uuid4(),
    )
    assert response.status == "queued"
    assert response.details["catch_up_week"] is True


@pytest.mark.asyncio
async def test_target_selection_excludes_existing_today(monkeypatch):
    dates = [WEEK + timedelta(days=i) for i in range(4)]
    monkeypatch.setattr(worker, "SessionFactory", lambda: Session(uuid.uuid4(), dates, [dates[-1]]))
    assert await worker._manual_daily_brief_targets(week_id=WEEK, through=dates[-1]) == tuple(
        dates[:3]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_day,cancel", [(None, False), (8, False), (None, True)])
async def test_manual_catchup_order_failure_isolation_and_cancel(monkeypatch, failed_day, cancel):
    claimed = SimpleNamespace(
        id=str(uuid.uuid4()),
        week_id=WEEK,
        job_type="daily_brief",
        details={"trade_date": "2026-09-10", "catch_up_week": True},
    )
    application = SimpleNamespace(
        claim_next_replay_job=AsyncMock(return_value=None),
        claim_next_output_job=AsyncMock(return_value=claimed),
    )
    monkeypatch.setattr(worker, "SessionFactory", Session)
    monkeypatch.setattr(worker, "SqlJobApplication", lambda session: application)
    monkeypatch.setattr(worker, "_manual_daily_catchup_gate", AsyncMock(return_value=None))
    monkeypatch.setattr(
        worker,
        "_manual_daily_brief_targets",
        AsyncMock(return_value=(WEEK, date(2026, 9, 8), date(2026, 9, 9))),
    )
    calls = []

    async def generate(*, now, trade_date):
        calls.append(trade_date.day)
        if trade_date.day == failed_day:
            raise RuntimeError("provider unavailable")
        return SimpleNamespace(items=[1], quality=SimpleNamespace(value="verified"))

    monkeypatch.setattr(worker, "execute_daily_brief", generate)
    monkeypatch.setattr(
        worker,
        "_output_job_cancel_requested",
        AsyncMock(side_effect=lambda _: cancel and bool(calls)),
    )
    monkeypatch.setattr(worker, "_update_output_progress", AsyncMock())
    success, failure = AsyncMock(), AsyncMock()
    monkeypatch.setattr(worker, "_finish_output_success", success)
    monkeypatch.setattr(worker, "_finish_output_failure_with_details", failure)
    await worker.execute_next_queued_output_job(
        now=datetime(2026, 9, 10, 18, tzinfo=worker.SHANGHAI)
    )
    assert calls == ([7] if cancel else [7, 8, 9])
    if failed_day or cancel:
        success.assert_not_called()
        outcomes = failure.call_args.args[-1]["daily_outcomes"]
        assert len(outcomes) == len(calls)
        if failed_day:
            assert [item["status"] for item in outcomes] == ["generated", "failed", "generated"]
    else:
        success.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("day,hour,expected", [(10, 14, 9), (12, 18, 12)])
async def test_catchup_before_close_and_weekend(monkeypatch, day, hour, expected):
    claimed = SimpleNamespace(
        id=str(uuid.uuid4()),
        week_id=WEEK,
        job_type="daily_brief",
        details={"trade_date": f"2026-09-{day}", "catch_up_week": True},
    )
    monkeypatch.setattr(worker, "SessionFactory", Session)
    monkeypatch.setattr(
        worker,
        "SqlJobApplication",
        lambda _: SimpleNamespace(
            claim_next_replay_job=AsyncMock(return_value=None),
            claim_next_output_job=AsyncMock(return_value=claimed),
        ),
    )
    monkeypatch.setattr(worker, "_manual_daily_catchup_gate", AsyncMock(return_value=None))
    targets = AsyncMock(return_value=(WEEK,))
    monkeypatch.setattr(worker, "_manual_daily_brief_targets", targets)
    generate = AsyncMock(
        return_value=SimpleNamespace(items=[], quality=SimpleNamespace(value="verified"))
    )
    monkeypatch.setattr(worker, "execute_daily_brief", generate)
    monkeypatch.setattr(worker, "_update_output_progress", AsyncMock())
    monkeypatch.setattr(worker, "_output_job_cancel_requested", AsyncMock(return_value=False))
    monkeypatch.setattr(worker, "_finish_output_success", AsyncMock())
    await worker.execute_next_queued_output_job(
        now=datetime(2026, 9, day, hour, tzinfo=worker.SHANGHAI)
    )
    assert targets.call_args.kwargs["through"] == date(2026, 9, expected)
    assert generate.call_args.kwargs["trade_date"] == WEEK


@pytest.mark.asyncio
async def test_catchup_rejects_expired_week_and_incomplete_calendar(monkeypatch):
    monkeypatch.setattr(
        worker,
        "_next_trading_week_start",
        AsyncMock(return_value=datetime(2026, 9, 14, tzinfo=worker.SHANGHAI)),
    )
    monkeypatch.setattr(worker, "SessionFactory", lambda: Session([WEEK]))
    expired = await worker._manual_daily_catchup_gate(
        WEEK, datetime(2026, 9, 14, tzinfo=worker.SHANGHAI)
    )
    incomplete = await worker._manual_daily_catchup_gate(
        WEEK, datetime(2026, 9, 10, tzinfo=worker.SHANGHAI)
    )
    assert expired and "历史" in expired[1]
    assert incomplete and "日历不完整" in incomplete[1]
