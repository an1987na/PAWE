import uuid
from datetime import UTC, date, datetime
from typing import Literal

from fastapi.testclient import TestClient
from pawe_api.auth.dependencies import require_admin, require_csrf
from pawe_api.auth.repository import Principal
from pawe_api.contracts import (
    JobResponse,
    ManualOutputJobRequest,
    UserResponse,
    WeeklySelectionJobRequest,
)
from pawe_api.main import app, get_job_application

WEEK_ID = date(2026, 8, 3)
ADMIN = Principal(
    UserResponse(
        id=str(uuid.uuid4()),
        username="admin",
        role="admin",
        is_active=True,
        created_at=datetime.now(UTC),
    ),
    uuid.uuid4(),
    "csrf",
)


class FakeJobApplication:
    async def request_cancel(self, job_id: uuid.UUID, actor_id: uuid.UUID) -> JobResponse | None:
        return self._response(WEEK_ID).model_copy(update={"id": str(job_id), "status": "cancelled"})

    async def list_week_jobs(self, week_id: date) -> list[JobResponse]:
        return [self._response(week_id)]

    async def trigger_weekly_selection(
        self,
        request: WeeklySelectionJobRequest,
        actor_id: uuid.UUID,
    ) -> JobResponse:
        return self._response(request.week_id)

    async def enqueue_weekly_selection(
        self,
        request: WeeklySelectionJobRequest,
        actor_id: uuid.UUID,
    ) -> JobResponse:
        return self._response(request.week_id)

    async def enqueue_output_job(
        self,
        request: ManualOutputJobRequest,
        actor_id: uuid.UUID,
    ) -> JobResponse:
        return self._response(request.week_id, job_type=request.job_type)

    @staticmethod
    def _response(
        week_id: date,
        *,
        job_type: Literal["weekly_selection", "daily_brief", "weekly_review"] = "weekly_selection",
    ) -> JobResponse:
        return JobResponse(
            id=str(uuid.uuid4()),
            job_type=job_type,
            week_id=week_id,
            status="failed",
            stage="snapshot_gate",
            error_code="SNAPSHOT_MISSING",
            error_message="No locked pre-decision snapshot is available for this week",
            created_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )


def test_admin_can_trigger_audited_weekly_job() -> None:
    app.dependency_overrides[get_job_application] = lambda: FakeJobApplication()
    app.dependency_overrides[require_csrf] = lambda: ADMIN
    app.dependency_overrides[require_admin] = lambda: ADMIN
    try:
        response = TestClient(app).post(
            "/api/v1/jobs/weekly-selection",
            json={"week_id": "2026-08-03", "idempotency_key": "weekly-job-001"},
        )
        assert response.status_code == 201
        assert response.json()["error_code"] == "SNAPSHOT_MISSING"

        history = TestClient(app).get("/api/v1/weeks/2026-08-03/jobs")
        assert history.status_code == 200
        assert history.json()[0]["stage"] == "snapshot_gate"
    finally:
        app.dependency_overrides.clear()


def test_weekly_job_rejects_non_monday_week_id() -> None:
    app.dependency_overrides[require_csrf] = lambda: ADMIN
    try:
        response = TestClient(app).post(
            "/api/v1/jobs/weekly-selection",
            json={"week_id": "2026-08-04", "idempotency_key": "weekly-job-002"},
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_admin_can_enqueue_daily_brief_and_weekly_review_jobs() -> None:
    app.dependency_overrides[get_job_application] = lambda: FakeJobApplication()
    app.dependency_overrides[require_csrf] = lambda: ADMIN
    try:
        daily = TestClient(app).post(
            "/api/v1/jobs/output",
            json={
                "job_type": "daily_brief",
                "week_id": "2026-08-10",
                "trade_date": "2026-08-11",
                "idempotency_key": "daily-job-001",
            },
        )
        review = TestClient(app).post(
            "/api/v1/jobs/output",
            json={
                "job_type": "weekly_review",
                "week_id": "2026-08-10",
                "idempotency_key": "review-job-001",
            },
        )
        assert daily.status_code == 201
        assert daily.json()["job_type"] == "daily_brief"
        assert review.status_code == 201
        assert review.json()["job_type"] == "weekly_review"
    finally:
        app.dependency_overrides.clear()


def test_daily_job_requires_trade_date_in_same_natural_week() -> None:
    app.dependency_overrides[require_csrf] = lambda: ADMIN
    try:
        missing = TestClient(app).post(
            "/api/v1/jobs/output",
            json={
                "job_type": "daily_brief",
                "week_id": "2026-08-10",
                "idempotency_key": "daily-job-002",
            },
        )
        outside = TestClient(app).post(
            "/api/v1/jobs/output",
            json={
                "job_type": "daily_brief",
                "week_id": "2026-08-10",
                "trade_date": "2026-08-17",
                "idempotency_key": "daily-job-003",
            },
        )
        assert missing.status_code == 422
        assert outside.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_admin_can_request_cooperative_job_cancellation() -> None:
    fake = FakeJobApplication()
    job_id = uuid.uuid4()
    app.dependency_overrides[get_job_application] = lambda: fake
    app.dependency_overrides[require_csrf] = lambda: ADMIN
    try:
        response = TestClient(app).post(f"/api/v1/jobs/{job_id}/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
    finally:
        app.dependency_overrides.clear()
