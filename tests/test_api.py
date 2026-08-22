from fastapi.testclient import TestClient
from pawe_api.main import app

client = TestClient(app)


def test_health_is_explicit_about_disabled_ai() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "environment": "development",
        "ai_enabled": False,
        "ai_model": "gpt-5.6-sol",
    }


def test_current_week_requires_authentication() -> None:
    response = client.get("/api/v1/weeks/current")
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"
