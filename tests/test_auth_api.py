import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from pawe_api.auth.dependencies import get_auth_application, get_current_principal, require_csrf
from pawe_api.auth.repository import IssuedSession, Principal
from pawe_api.contracts import CreateUserRequest, UpdateUserRequest, UserResponse
from pawe_api.main import app
from sqlalchemy.exc import SQLAlchemyError

ADMIN_USER = UserResponse(
    id=str(uuid.uuid4()),
    username="admin",
    role="admin",
    is_active=True,
    created_at=datetime.now(UTC),
)
VIEWER_USER = UserResponse(
    id=str(uuid.uuid4()),
    username="reader",
    role="viewer",
    is_active=True,
    created_at=datetime.now(UTC),
)
ADMIN = Principal(ADMIN_USER, uuid.uuid4(), "csrf-hash")
VIEWER = Principal(VIEWER_USER, uuid.uuid4(), "csrf-hash")


class FakeAuthApplication:
    def __init__(self) -> None:
        self.logged_out = False

    async def login(self, username: str, password: str, ttl_hours: int) -> IssuedSession | None:
        if username != "admin" or password != "correct-password":
            return None
        return IssuedSession(ADMIN_USER, "session-secret", "csrf-secret")

    async def resolve(self, session_token: str) -> Principal | None:
        return ADMIN if session_token == "session-secret" else None

    async def logout(self, session_id: uuid.UUID) -> None:
        self.logged_out = True

    async def list_users(self) -> list[UserResponse]:
        return [ADMIN_USER, VIEWER_USER]

    async def create_user(
        self, request: CreateUserRequest, created_by: uuid.UUID
    ) -> UserResponse:
        return VIEWER_USER.model_copy(update={"username": request.username.lower()})

    async def update_user(
        self, user_id: uuid.UUID, request: UpdateUserRequest, actor_id: uuid.UUID
    ) -> UserResponse | None:
        if str(user_id) != VIEWER_USER.id:
            return None
        return VIEWER_USER.model_copy(update={"is_active": request.is_active})


class UnavailableAuthApplication(FakeAuthApplication):
    async def login(self, username: str, password: str, ttl_hours: int) -> IssuedSession | None:
        raise SQLAlchemyError("database details must not reach the client")


def test_login_sets_http_only_session_and_csrf_cookies() -> None:
    fake = FakeAuthApplication()
    app.dependency_overrides[get_auth_application] = lambda: fake
    try:
        response = TestClient(app).post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        assert response.status_code == 200
        cookies = response.headers.get_list("set-cookie")
        session_cookie = next(cookie for cookie in cookies if cookie.startswith("pawe_session="))
        csrf_cookie = next(cookie for cookie in cookies if cookie.startswith("pawe_csrf="))
        assert "pawe_session=session-secret" in session_cookie and "HttpOnly" in session_cookie
        assert "pawe_csrf=csrf-secret" in csrf_cookie and "HttpOnly" not in csrf_cookie
        assert response.json()["user"]["role"] == "admin"
    finally:
        app.dependency_overrides.clear()


def test_invalid_login_does_not_reveal_which_credential_failed() -> None:
    app.dependency_overrides[get_auth_application] = lambda: FakeAuthApplication()
    try:
        response = TestClient(app).post(
            "/api/v1/auth/login", json={"username": "unknown", "password": "wrong"}
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid username or password"
    finally:
        app.dependency_overrides.clear()


def test_login_maps_database_failure_to_safe_service_unavailable() -> None:
    app.dependency_overrides[get_auth_application] = lambda: UnavailableAuthApplication()
    try:
        response = TestClient(app).post(
            "/api/v1/auth/login", json={"username": "admin", "password": "not-relevant"}
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "Authentication service is temporarily unavailable"
        assert "database details" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_admin_can_create_viewer_but_viewer_cannot_manage_users() -> None:
    fake = FakeAuthApplication()
    app.dependency_overrides[get_auth_application] = lambda: fake
    app.dependency_overrides[require_csrf] = lambda: ADMIN
    try:
        response = TestClient(app).post(
            "/api/v1/users",
            json={"username": "New.Reader", "password": "long-reader-password"},
        )
        assert response.status_code == 201
        assert response.json()["username"] == "new.reader"
        assert response.json()["role"] == "viewer"

        app.dependency_overrides[require_csrf] = lambda: VIEWER
        forbidden = TestClient(app).post(
            "/api/v1/users",
            json={"username": "other", "password": "another-long-password"},
        )
        assert forbidden.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_authenticated_viewer_can_read_session() -> None:
    app.dependency_overrides[get_current_principal] = lambda: VIEWER
    try:
        response = TestClient(app).get("/api/v1/auth/me")
        assert response.status_code == 200
        assert response.json()["user"]["role"] == "viewer"
    finally:
        app.dependency_overrides.clear()
