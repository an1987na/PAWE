import uuid
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient
from pawe_api.auth.dependencies import get_current_principal
from pawe_api.auth.repository import Principal
from pawe_api.contracts import (
    DailyBrief,
    DailyBriefItem,
    DailyRiskStatus,
    DataQuality,
    UserResponse,
)
from pawe_api.main import app, get_brief_application

WEEK_ID = date(2026, 8, 10)


class FakeBriefApplication:
    async def list_week(self, week_id: date) -> list[DailyBrief]:
        assert week_id == WEEK_ID
        return [
            DailyBrief(
                week_id=week_id,
                trade_date=date(2026, 8, 11),
                decision_version=2,
                as_of=datetime(2026, 8, 11, 15, tzinfo=UTC),
                fetched_at=datetime(2026, 8, 11, 17, 30, tzinfo=UTC),
                quality=DataQuality.VERIFIED,
                ai_degraded=True,
                items=[
                    DailyBriefItem(
                        stock_code="000001",
                        stock_name="样本",
                        daily_return=0.01,
                        week_to_date_return=0.02,
                        week_high_return=0.03,
                        drawdown_from_week_high=-0.01,
                        distance_to_target=0.07,
                        volume_activity=1.1,
                        risk_status=DailyRiskStatus.WATCH,
                        summary="确定性简报",
                    )
                ],
            )
        ]


def test_weekly_briefs_are_loaded_from_application() -> None:
    principal = Principal(
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
    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_brief_application] = lambda: FakeBriefApplication()
    try:
        response = TestClient(app).get(f"/api/v1/weeks/{WEEK_ID.isoformat()}/briefs")
        assert response.status_code == 200
        assert response.json()[0]["items"][0]["summary"] == "确定性简报"
    finally:
        app.dependency_overrides.clear()
