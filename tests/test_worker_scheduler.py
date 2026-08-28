from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pawe_worker.main as worker
import pytest
from pawe_api.config import Settings
from pawe_worker.main import (
    add_startup_catchups,
    build_scheduler,
    missing_daily_brief_targets,
    natural_week_id,
    upcoming_week_id,
)


def test_worker_registers_preopen_and_daily_brief_jobs() -> None:
    scheduler = build_scheduler(Settings(_env_file=None))
    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == {
        "weekly-job-runner",
        "weekly-data-preparation",
        "weekly-preopen",
        "daily-brief",
    }
    assert "interval[0:00:02]" in str(jobs["weekly-job-runner"].trigger)
    assert "day_of_week='sun'" in str(jobs["weekly-data-preparation"].trigger)
    assert "hour='18'" in str(jobs["weekly-data-preparation"].trigger)
    assert "hour='8'" in str(jobs["weekly-preopen"].trigger)
    assert "minute='30'" in str(jobs["weekly-preopen"].trigger)
    assert "hour='15'" in str(jobs["daily-brief"].trigger)
    assert "minute='30'" in str(jobs["daily-brief"].trigger)


def test_worker_reserves_capacity_for_long_data_preparation() -> None:
    scheduler = build_scheduler(Settings(_env_file=None))

    executor = scheduler._executors["default"]
    assert executor._pool._max_workers == 3


def test_worker_uses_natural_monday_for_holiday_shifted_week() -> None:
    assert natural_week_id(date(2026, 8, 10)) == date(2026, 8, 10)
    assert natural_week_id(date(2026, 8, 11)) == date(2026, 8, 10)


def test_worker_prepares_the_immediately_upcoming_natural_week() -> None:
    assert upcoming_week_id(date(2026, 8, 9)) == date(2026, 8, 10)
    assert upcoming_week_id(date(2026, 8, 10)) == date(2026, 8, 10)


@pytest.mark.asyncio
async def test_weekly_review_waits_until_all_daily_briefs_are_complete(monkeypatch) -> None:
    week_id = date(2026, 8, 17)
    now = datetime(2026, 8, 21, 15, 31, tzinfo=ZoneInfo("Asia/Shanghai"))
    calls: list[date] = []

    async def fake_due_at(target_week: date) -> datetime:
        assert target_week == week_id
        return datetime(2026, 8, 21, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    async def incomplete(target_week: date) -> bool:
        assert target_week == week_id
        return False

    async def fake_review(*, now: datetime, week_id: date):
        del now
        calls.append(week_id)
        return [object()]

    async def no_review(target_week: date) -> bool:
        assert target_week == week_id
        return False

    monkeypatch.setattr(worker, "_weekly_review_due_at", fake_due_at)
    monkeypatch.setattr(worker, "_formal_daily_briefs_complete", incomplete)
    monkeypatch.setattr(worker, "_weekly_review_exists", no_review)
    monkeypatch.setattr(worker, "execute_weekly_review", fake_review)

    assert await worker.execute_weekly_review_after_daily_brief(now=now, week_id=week_id) == []
    assert calls == []


@pytest.mark.asyncio
async def test_weekly_review_runs_immediately_after_daily_briefs_are_complete(monkeypatch) -> None:
    week_id = date(2026, 8, 17)
    now = datetime(2026, 8, 21, 15, 31, tzinfo=ZoneInfo("Asia/Shanghai"))
    result = [object()]

    async def fake_due_at(target_week: date) -> datetime:
        assert target_week == week_id
        return datetime(2026, 8, 21, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    async def complete(target_week: date) -> bool:
        assert target_week == week_id
        return True

    async def fake_review(*, now: datetime, week_id: date):
        assert now == datetime(2026, 8, 21, 15, 31, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert week_id == date(2026, 8, 17)
        return result

    async def no_review(target_week: date) -> bool:
        assert target_week == week_id
        return False

    monkeypatch.setattr(worker, "_weekly_review_due_at", fake_due_at)
    monkeypatch.setattr(worker, "_formal_daily_briefs_complete", complete)
    monkeypatch.setattr(worker, "_weekly_review_exists", no_review)
    monkeypatch.setattr(worker, "execute_weekly_review", fake_review)

    assert await worker.execute_weekly_review_after_daily_brief(now=now, week_id=week_id) == result


def test_daily_brief_catchup_finds_only_missing_days_of_completed_weeks() -> None:
    week_id = date(2026, 8, 10)
    older_week_id = date(2026, 8, 3)
    open_dates = tuple(week_id + timedelta(days=offset) for offset in range(5))
    older_open_dates = tuple(older_week_id + timedelta(days=offset) for offset in range(5))

    targets = missing_daily_brief_targets(
        target_week=week_id,
        published_decisions={week_id: "published-v2", older_week_id: "published-v1"},
        open_dates_by_week={week_id: open_dates, older_week_id: older_open_dates},
        calendar_dates_by_week={week_id: open_dates, older_week_id: older_open_dates},
        active_briefs={
            (week_id, "published-v2", date(2026, 8, 10)),
            (week_id, "published-v2", date(2026, 8, 11)),
            (week_id, "published-v2", date(2026, 8, 14)),
        },
        today=date(2026, 8, 18),
    )

    assert targets == ((week_id, date(2026, 8, 12)), (week_id, date(2026, 8, 13)))


def test_daily_brief_catchup_skips_current_incomplete_week() -> None:
    week_id = date(2026, 8, 17)
    targets = missing_daily_brief_targets(
        target_week=week_id,
        published_decisions={week_id: "published-v1"},
        open_dates_by_week={week_id: (week_id, date(2026, 8, 18))},
        calendar_dates_by_week={week_id: (week_id, date(2026, 8, 18))},
        active_briefs=set(),
        today=date(2026, 8, 18),
    )

    assert targets == ()


def test_daily_brief_catchup_skips_incomplete_calendar() -> None:
    week_id = date(2026, 8, 10)
    targets = missing_daily_brief_targets(
        target_week=week_id,
        published_decisions={week_id: "published-v2"},
        open_dates_by_week={week_id: (week_id, date(2026, 8, 11), date(2026, 8, 12))},
        calendar_dates_by_week={week_id: (week_id, date(2026, 8, 11), date(2026, 8, 12))},
        active_briefs=set(),
        today=date(2026, 8, 18),
    )

    assert targets == ()


@pytest.mark.asyncio
async def test_daily_brief_catchup_isolates_a_failed_trade_date(monkeypatch) -> None:
    week_id = date(2026, 8, 10)
    failed_date = date(2026, 8, 12)
    generated_date = date(2026, 8, 13)

    async def fake_targets(*, today: date) -> tuple[tuple[date, date], ...]:
        del today
        return ((week_id, failed_date), (week_id, generated_date))

    async def fake_generate(*, now: datetime, trade_date: date):
        del now
        if trade_date == failed_date:
            raise RuntimeError("one provider failed")
        return object()

    monkeypatch.setattr(worker, "_missing_daily_brief_targets", fake_targets)
    monkeypatch.setattr(worker, "execute_daily_brief", fake_generate)
    outcomes = await worker.execute_daily_brief_catchup(
        now=datetime(2026, 8, 22, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    )

    assert outcomes == (
        (week_id, failed_date, "failed:RuntimeError"),
        (week_id, generated_date, "generated"),
    )


def test_worker_adds_daily_brief_catchup_after_due_time_only() -> None:
    settings = Settings(_env_file=None)
    timezone = ZoneInfo("Asia/Shanghai")

    before_due = build_scheduler(settings)
    add_startup_catchups(
        before_due,
        settings,
        now=datetime(2026, 8, 10, 15, 29, tzinfo=timezone),
    )
    assert "daily-brief-startup-catchup" not in {
        job.id for job in before_due.get_jobs()
    }
    assert "weekly-preopen-startup-catchup" not in {
        job.id for job in before_due.get_jobs()
    }
    assert "weekly-review-startup-catchup" in {
        job.id for job in before_due.get_jobs()
    }

    after_due = build_scheduler(settings)
    add_startup_catchups(
        after_due,
        settings,
        now=datetime(2026, 8, 10, 15, 31, tzinfo=timezone),
    )
    assert "daily-brief-startup-catchup" in {
        job.id for job in after_due.get_jobs()
    }

    weekend = build_scheduler(settings)
    add_startup_catchups(
        weekend,
        settings,
        now=datetime(2026, 8, 15, 16, 0, tzinfo=timezone),
    )
    assert "daily-brief-startup-catchup" not in {
        job.id for job in weekend.get_jobs()
    }
    assert "weekly-review-startup-catchup" in {
        job.id for job in weekend.get_jobs()
    }


def test_worker_recovers_sunday_preparation_after_its_due_time() -> None:
    settings = Settings(_env_file=None)
    timezone = ZoneInfo("Asia/Shanghai")
    scheduler = build_scheduler(settings)
    add_startup_catchups(
        scheduler,
        settings,
        now=datetime(2026, 8, 16, 18, 1, tzinfo=timezone),
    )
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "weekly-data-preparation-startup-catchup" in job_ids
    assert "weekly-review-startup-catchup" in job_ids


def test_worker_recovers_monday_preopen_before_publication_deadline() -> None:
    settings = Settings(_env_file=None)
    timezone = ZoneInfo("Asia/Shanghai")
    scheduler = build_scheduler(settings)
    add_startup_catchups(
        scheduler,
        settings,
        now=datetime(2026, 8, 17, 8, 0, tzinfo=timezone),
    )
    assert "weekly-preopen-startup-catchup" in {
        job.id for job in scheduler.get_jobs()
    }
