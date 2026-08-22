import uuid
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient
from pawe_api.auth.dependencies import get_current_principal
from pawe_api.auth.repository import Principal
from pawe_api.contracts import DataQuality, UserResponse, WeeklyReviewResponse
from pawe_api.evaluation.repository import archive_week_statement
from pawe_api.main import app, get_weekly_review_application
from sqlalchemy.dialects import postgresql


class FakeWeeklyReviewApplication:
    async def list_archive_weeks(self) -> list[date]:
        return [date(2026, 8, 10), date(2026, 8, 3)]

    async def list_all(self) -> list[WeeklyReviewResponse]:
        return [_review()]

    async def list_week(self, week_id: date) -> list[WeeklyReviewResponse]:
        return [_review()] if week_id == date(2026, 8, 3) else []

    async def latest(self) -> WeeklyReviewResponse | None:
        return _review()


def _review() -> WeeklyReviewResponse:
    return WeeklyReviewResponse(
        id=str(uuid.uuid4()),
        week_id=date(2026, 8, 3),
        source_type="historical_replay",
        source_version=1,
        rule_version="v9.0.0",
        status="degraded",
        entry_trade_date=date(2026, 8, 3),
        final_trade_date=date(2026, 8, 7),
        as_of=datetime(2026, 8, 7, 17, 30, tzinfo=UTC),
        generated_at=datetime(2026, 8, 10, 9, tzinfo=UTC),
        quality=DataQuality.DEGRADED,
        aggregate={"item_count": 0},
        summary="历史周复盘",
        warnings=["RETROSPECTIVE_FETCH_AFTER_SIMULATED_TIME"],
        items=[],
    )


PRINCIPAL = Principal(
    user=UserResponse(
        id=str(uuid.uuid4()),
        username="viewer",
        role="viewer",
        is_active=True,
        created_at=datetime.now(UTC),
    ),
    session_id=uuid.uuid4(),
    csrf_token_hash="unused",
)


def test_all_weekly_reviews_are_available_to_authenticated_viewers() -> None:
    app.dependency_overrides[get_weekly_review_application] = lambda: FakeWeeklyReviewApplication()
    app.dependency_overrides[get_current_principal] = lambda: PRINCIPAL
    try:
        response = TestClient(app).get("/api/v1/reviews")
        assert response.status_code == 200
        assert response.json()[0]["week_id"] == "2026-08-03"
        assert response.json()[0]["source_type"] == "historical_replay"
    finally:
        app.dependency_overrides.clear()


def test_archive_index_includes_a_week_before_its_review_is_available() -> None:
    app.dependency_overrides[get_weekly_review_application] = lambda: FakeWeeklyReviewApplication()
    app.dependency_overrides[get_current_principal] = lambda: PRINCIPAL
    try:
        response = TestClient(app).get("/api/v1/history/weeks")
        assert response.status_code == 200
        assert response.json() == ["2026-08-10", "2026-08-03"]
    finally:
        app.dependency_overrides.clear()


def test_archive_index_statement_includes_only_completed_replay_runs() -> None:
    sql = str(
        archive_week_statement().compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).lower()
    assert "replay_runs" in sql
    assert "replay_runs.status = 'succeeded'" in sql
    assert "replay_runs.requested_stage = 'weekly_review'" in sql
    assert "replay_stage_runs" not in sql
