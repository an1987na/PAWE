import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pawe_api.main as main_module
from fastapi.testclient import TestClient
from pawe_api.auth.dependencies import get_current_principal, require_csrf
from pawe_api.auth.repository import Principal
from pawe_api.contracts import AIConnectionCheckResponse, UserResponse
from pawe_api.db.session import get_db_session
from pawe_api.main import app
from pytest import MonkeyPatch

USER = UserResponse(
    id=str(uuid.uuid4()),
    username="reader",
    role="viewer",
    is_active=True,
    created_at=datetime.now(UTC),
)
PRINCIPAL = Principal(USER, uuid.uuid4(), "csrf-hash")


def test_personal_ai_connection_never_returns_full_key(monkeypatch: MonkeyPatch) -> None:
    row = SimpleNamespace(
        key_hint="••••7890",
        model="gpt-5.6-sol",
        updated_at=datetime.now(UTC),
    )

    async def fake_get(_session: object, user_id: uuid.UUID) -> object:
        assert str(user_id) == USER.id
        return row

    async def fake_save(
        _session: object,
        user_id: uuid.UUID,
        api_key: str,
        model: str,
        _settings: object,
    ) -> object:
        assert str(user_id) == USER.id
        assert api_key == "sk-test-personal-key-1234567890"
        assert model == "gpt-5.6-sol"
        return row

    monkeypatch.setattr(main_module, "get_user_credential", fake_get)
    monkeypatch.setattr(main_module, "save_user_credential", fake_save)
    app.dependency_overrides[get_current_principal] = lambda: PRINCIPAL
    app.dependency_overrides[require_csrf] = lambda: PRINCIPAL
    app.dependency_overrides[get_db_session] = lambda: object()
    try:
        client = TestClient(app)
        get_response = client.get("/api/v1/ai/connection")
        assert get_response.status_code == 200
        assert get_response.json()["key_hint"] == "••••7890"
        assert "personal-key" not in get_response.text

        save_response = client.post(
            "/api/v1/ai/connection",
            json={"api_key": "sk-test-personal-key-1234567890", "model": "gpt-5.6-sol"},
        )
        assert save_response.status_code == 200
        assert save_response.json()["source"] == "personal_api_key"
        assert "personal-key" not in save_response.text
    finally:
        app.dependency_overrides.clear()


def test_connection_probe_uses_current_actor_without_exposing_key() -> None:
    class Probe:
        async def check_connection(
            self, _session: object, *, actor_id: uuid.UUID
        ) -> AIConnectionCheckResponse:
            assert str(actor_id) == USER.id
            return AIConnectionCheckResponse(
                status="failed",
                model="gpt-6.1-sol",
                latency_ms=1,
                error_code="OPENAI_AUTH_FAILED",
                message="OpenAI API Key 无效或已撤销，请重新关联。",
            )

    app.dependency_overrides[require_csrf] = lambda: PRINCIPAL
    app.dependency_overrides[get_db_session] = lambda: object()
    app.dependency_overrides[main_module.get_ai_service] = lambda: Probe()
    try:
        response = TestClient(app).post("/api/v1/ai/connection/test")
        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        assert response.json()["error_code"] == "OPENAI_AUTH_FAILED"
        assert "api_key" not in response.json()
    finally:
        app.dependency_overrides.clear()


def test_connection_probe_requires_authentication() -> None:
    app.dependency_overrides[get_db_session] = lambda: object()
    try:
        assert TestClient(app).post("/api/v1/ai/connection/test").status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_connection_probe_requires_csrf_even_when_authenticated() -> None:
    app.dependency_overrides[get_current_principal] = lambda: PRINCIPAL
    app.dependency_overrides[get_db_session] = lambda: object()
    try:
        assert TestClient(app).post("/api/v1/ai/connection/test").status_code == 403
    finally:
        app.dependency_overrides.clear()
