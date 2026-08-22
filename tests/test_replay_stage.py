import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pawe_api.auth.dependencies import get_current_principal, require_csrf
from pawe_api.auth.repository import Principal
from pawe_api.contracts import (
    CalendarPreparationResponse,
    JobResponse,
    ReplayEligibilityResponse,
    ReplayJobRequest,
    ReplayRunResponse,
    UserResponse,
)
from pawe_api.db import models
from pawe_api.db.base import Base
from pawe_api.main import app, get_replay_application
from pawe_api.replay_stage.calculation import point_in_time_payload
from pawe_api.replay_stage.repository import due_daily_brief_dates
from pawe_api.replay_stage.windows import (
    ReplayStage,
    ReplayWindowError,
    classify_replay_window,
    replay_stage_order,
)

WEEK_ID = date(2026, 8, 10)
ADMIN = Principal(
    UserResponse(
        id=str(uuid.uuid4()),
        username="admin",
        role="admin",
        is_active=True,
        created_at=datetime.now(UTC),
    ),
    uuid.uuid4(),
    "csrf-hash",
)
VIEWER = Principal(
    UserResponse(
        id=str(uuid.uuid4()),
        username="viewer",
        role="viewer",
        is_active=True,
        created_at=datetime.now(UTC),
    ),
    uuid.uuid4(),
    "csrf-hash",
)


def test_replay_windows_switch_at_the_declared_boundaries() -> None:
    before_weekly = classify_replay_window(
        ReplayStage.WEEKLY_SELECTION,
        now=datetime(2026, 8, 16, 15, 59, tzinfo=UTC),
        week_id=WEEK_ID,
        first_open_date=date(2026, 8, 10),
        previous_open_date=date(2026, 8, 7),
        next_trading_week_start=date(2026, 8, 17),
    )
    after_weekly = classify_replay_window(
        ReplayStage.WEEKLY_SELECTION,
        now=datetime(2026, 8, 16, 16, 0, tzinfo=UTC),
        week_id=WEEK_ID,
        first_open_date=date(2026, 8, 10),
        previous_open_date=date(2026, 8, 7),
        next_trading_week_start=date(2026, 8, 17),
    )
    assert before_weekly.mode == "formal"
    assert after_weekly.mode == "replay"
    assert after_weekly.simulated_cutoff == datetime(
        2026, 8, 7, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai")
    )

    same_day = classify_replay_window(
        ReplayStage.DAILY_BRIEF,
        now=datetime(2026, 8, 13, 7, 30, tzinfo=UTC),
        week_id=WEEK_ID,
        trade_date=date(2026, 8, 13),
    )
    next_day = classify_replay_window(
        ReplayStage.DAILY_BRIEF,
        now=datetime(2026, 8, 13, 16, 0, tzinfo=UTC),
        week_id=WEEK_ID,
        trade_date=date(2026, 8, 13),
        next_trading_week_start=date(2026, 8, 17),
    )
    assert same_day.mode == "formal"
    assert next_day.mode == "formal"

    same_week = classify_replay_window(
        ReplayStage.WEEKLY_REVIEW,
        now=datetime(2026, 8, 14, 9, 30, tzinfo=UTC),
        week_id=WEEK_ID,
        final_open_date=date(2026, 8, 14),
        next_trading_week_start=date(2026, 8, 17),
    )
    next_week = classify_replay_window(
        ReplayStage.WEEKLY_REVIEW,
        now=datetime(2026, 8, 16, 16, 0, tzinfo=UTC),
        week_id=WEEK_ID,
        final_open_date=date(2026, 8, 14),
        next_trading_week_start=date(2026, 8, 17),
    )
    assert same_week.mode == "formal"
    assert next_week.mode == "replay"


def test_replay_windows_reject_future_information() -> None:
    with pytest.raises(ReplayWindowError):
        classify_replay_window(
            ReplayStage.DAILY_BRIEF,
            now=datetime(2026, 8, 13, 7, 29, tzinfo=UTC),
            week_id=WEEK_ID,
            trade_date=date(2026, 8, 13),
        )
    with pytest.raises(ReplayWindowError):
        classify_replay_window(
            ReplayStage.WEEKLY_REVIEW,
            now=datetime(2026, 8, 14, 7, 29, tzinfo=UTC),
            week_id=WEEK_ID,
            final_open_date=date(2026, 8, 14),
        )
    assert replay_stage_order(ReplayStage.WEEKLY_REVIEW) == (
        ReplayStage.WEEKLY_SELECTION,
        ReplayStage.DAILY_BRIEF,
        ReplayStage.WEEKLY_REVIEW,
    )


def test_daily_eligibility_contains_only_dates_past_1530() -> None:
    open_dates = [
        date(2026, 8, 10),
        date(2026, 8, 11),
        date(2026, 8, 12),
        date(2026, 8, 13),
        date(2026, 8, 14),
    ]
    assert due_daily_brief_dates(open_dates, datetime(2026, 8, 12, 7, 29, tzinfo=UTC)) == (
        date(2026, 8, 10),
        date(2026, 8, 11),
    )
    assert due_daily_brief_dates(open_dates, datetime(2026, 8, 12, 7, 30, tzinfo=UTC)) == (
        date(2026, 8, 10),
        date(2026, 8, 11),
        date(2026, 8, 12),
    )


def test_selection_payload_fingerprint_boundary_ignores_future_bars() -> None:
    payload = {
        "source_bars": {
            "tencent": [
                {"trade_date": "2026-08-14", "close": "10"},
                {"trade_date": "2026-08-17", "close": "999"},
            ]
        }
    }
    bounded = point_in_time_payload(payload, as_of=date(2026, 8, 14))
    assert bounded["source_bars"] == {"tencent": [{"trade_date": "2026-08-14", "close": "10"}]}


def test_replay_tables_are_separate_from_formal_tables() -> None:
    assert {
        models.ReplayRun.__tablename__,
        models.ReplayStageRun.__tablename__,
        models.ReplayDecisionSet.__tablename__,
        models.ReplayDecisionItem.__tablename__,
        models.ReplayDailyBrief.__tablename__,
        models.ReplayDailyBriefItem.__tablename__,
        models.ReplayWeeklyReview.__tablename__,
        models.ReplayWeeklyReviewItem.__tablename__,
    } <= set(Base.metadata.tables)
    replay_item = Base.metadata.tables[models.ReplayDecisionItem.__tablename__]
    assert not any(
        foreign_key.column.table.name
        in {"decision_sets", "decision_items", "daily_briefs", "weekly_reviews"}
        for foreign_key in replay_item.foreign_keys
    )
    assert {"mode", "replay_stage", "trade_date", "replay_run_id"} <= set(
        Base.metadata.tables[models.Job.__tablename__].columns.keys()
    )


@pytest.mark.asyncio
async def test_replay_enqueue_flushes_parent_before_foreign_key_children() -> None:
    """The replay parent must be visible before staged rows and the job flush."""

    class ScalarRows:
        def __init__(self, rows: list[models.TradingCalendar]) -> None:
            self.rows = rows

        def __iter__(self):
            return iter(self.rows)

    class RecordingSession:
        def __init__(self, rows: list[models.TradingCalendar]) -> None:
            self.rows = rows
            self.added: list[object] = []
            self.events: list[tuple[str, tuple[str, ...]]] = []

        async def scalars(self, statement):  # noqa: ANN001 - test double
            del statement
            return ScalarRows(self.rows)

        async def scalar(self, statement):  # noqa: ANN001 - test double
            del statement
            return None

        def add(self, instance: object) -> None:
            self.added.append(instance)
            self.events.append(("add", (type(instance).__name__,)))

        async def flush(self) -> None:
            self.events.append(("flush", tuple(type(item).__name__ for item in self.added)))

        async def commit(self) -> None:
            self.events.append(("commit", ()))

    from pawe_api.replay_stage.repository import SqlReplayApplication

    open_dates = [date(2026, 8, day) for day in range(17, 22)]
    calendar = [
        models.TradingCalendar(
            trade_date=trade_date,
            is_open=True,
            previous_open_date=date(2026, 8, 14),
            source="test",
            quality="verified",
            fetched_at=datetime(2026, 8, 19, tzinfo=UTC),
            content_hash=f"calendar-{trade_date.isoformat()}",
        )
        for trade_date in open_dates
    ]
    session = RecordingSession(calendar)
    application = SqlReplayApplication(session)  # type: ignore[arg-type]

    await application.enqueue(
        ReplayJobRequest(
            stage="weekly_review",
            week_id=date(2026, 8, 17),
            idempotency_key="flush-order-regression",
        ),
        actor_id=uuid.uuid4(),
        now=datetime(2026, 8, 24, 12, tzinfo=UTC),
    )

    first_flush = next(index for index, event in enumerate(session.events) if event[0] == "flush")
    replay_add = next(
        index
        for index, event in enumerate(session.events)
        if event == ("add", (models.ReplayRun.__name__,))
    )
    first_flush_contents = session.events[first_flush][1]
    assert replay_add < first_flush
    assert first_flush_contents == (models.ReplayRun.__name__,)
    assert any(
        event == ("add", (models.ReplayStageRun.__name__,))
        for event in session.events[first_flush + 1 :]
    )
    assert any(
        event == ("add", (models.Job.__name__,)) for event in session.events[first_flush + 1 :]
    )
    stages = [item for item in session.added if isinstance(item, models.ReplayStageRun)]
    assert [stage.stage for stage in stages] == [
        "weekly_selection",
        "daily_brief",
        "daily_brief",
        "daily_brief",
        "daily_brief",
        "daily_brief",
        "weekly_review",
    ]
    assert [stage.trade_date for stage in stages[1:6]] == open_dates


class FakeReplayApplication:
    def __init__(self) -> None:
        self.enqueued: ReplayJobRequest | None = None
        self.calendar_ready = True

    async def prepare_calendar(self, week_id: date) -> CalendarPreparationResponse:
        return CalendarPreparationResponse(
            week_id=week_id,
            status="ready" if self.calendar_ready else "unavailable",
            warnings=[] if self.calendar_ready else ["CALENDAR_PREPARATION_FAILED"],
        )

    async def list_eligible_weeks(self, now: datetime) -> list[ReplayEligibilityResponse]:
        del now
        return [
            ReplayEligibilityResponse(
                week_id=WEEK_ID,
                stage="weekly_selection",
                trade_dates=[],
                formal_available=False,
                replay_available=True,
                reason="after_publication_deadline",
            )
        ]

    async def enqueue(
        self, request: ReplayJobRequest, actor_id: uuid.UUID, now: datetime
    ) -> JobResponse:
        del actor_id, now
        self.enqueued = request
        return JobResponse(
            id=str(uuid.uuid4()),
            job_type="replay",
            week_id=request.week_id,
            mode="replay",
            replay_stage=request.stage,
            trade_date=request.trade_date,
            replay_run_id=str(uuid.uuid4()),
            status="queued",
            stage="queued",
            created_at=datetime.now(UTC),
        )

    async def get_run(self, run_id: uuid.UUID) -> ReplayRunResponse | None:
        del run_id
        return None

    async def get_job(self, job_id: uuid.UUID) -> JobResponse | None:
        del job_id
        return None

    async def list_week(self, week_id: date) -> list[ReplayRunResponse]:
        del week_id
        return []


def test_replay_api_is_admin_only_and_exposes_eligible_weeks() -> None:
    fake = FakeReplayApplication()
    app.dependency_overrides[get_replay_application] = lambda: fake
    app.dependency_overrides[get_current_principal] = lambda: ADMIN
    app.dependency_overrides[require_csrf] = lambda: ADMIN
    try:
        eligible = TestClient(app).get("/api/v1/replays/eligible-weeks")
        assert eligible.status_code == 200
        assert eligible.json()[0]["replay_available"] is True

        queued = TestClient(app).post(
            "/api/v1/jobs/replay",
            json={
                "stage": "weekly_selection",
                "week_id": WEEK_ID.isoformat(),
                "idempotency_key": "replay-test-001",
            },
        )
        assert queued.status_code == 201
        assert queued.json()["mode"] == "replay"
        assert fake.enqueued is not None

        app.dependency_overrides[get_current_principal] = lambda: VIEWER
        forbidden = TestClient(app).get("/api/v1/replays/eligible-weeks")
        assert forbidden.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_replay_preparation_failure_does_not_create_a_job() -> None:
    fake = FakeReplayApplication()
    fake.calendar_ready = False
    app.dependency_overrides[get_replay_application] = lambda: fake
    app.dependency_overrides[get_current_principal] = lambda: ADMIN
    app.dependency_overrides[require_csrf] = lambda: ADMIN
    try:
        response = TestClient(app).post(
            "/api/v1/jobs/replay",
            json={
                "stage": "weekly_selection",
                "week_id": WEEK_ID.isoformat(),
                "idempotency_key": "replay-calendar-fail",
            },
        )
        assert response.status_code == 422
        assert fake.enqueued is None
    finally:
        app.dependency_overrides.clear()
