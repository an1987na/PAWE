import uuid
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient
from pawe_api.auth.dependencies import get_current_principal, require_csrf
from pawe_api.auth.repository import Principal
from pawe_api.contracts import (
    StockSearchResult,
    UserResponse,
    WatchlistItemResponse,
)
from pawe_api.main import app, get_watchlist_application
from pawe_api.watchlist.repository import WatchlistError

USER_ID = uuid.uuid4()
OTHER_USER_ID = uuid.uuid4()
WEEK_ID = date(2026, 8, 10)
VIEWER = Principal(
    user=UserResponse(
        id=str(USER_ID),
        username="reader",
        role="viewer",
        is_active=True,
        created_at=datetime.now(UTC),
    ),
    session_id=uuid.uuid4(),
    csrf_token_hash="csrf-hash",
)


class FakeWatchlistApplication:
    def __init__(self) -> None:
        self.user_ids: list[uuid.UUID] = []
        self.items: list[WatchlistItemResponse] = []

    async def search(self, user_id: uuid.UUID, query: str) -> list[StockSearchResult]:
        self.user_ids.append(user_id)
        return [
            StockSearchResult(
                stock_code="600519",
                stock_name="贵州茅台",
                exchange="SSE",
                board="main",
                already_followed=False,
            )
        ]

    async def list_active(self, user_id: uuid.UUID) -> list[WatchlistItemResponse]:
        self.user_ids.append(user_id)
        return self.items

    async def add(
        self, user_id: uuid.UUID, stock_code: str, *, now: datetime
    ) -> WatchlistItemResponse:
        self.user_ids.append(user_id)
        if len(self.items) >= 5:
            raise WatchlistError("每位用户最多关注5只标的")
        item = WatchlistItemResponse(
            id=str(uuid.uuid4()),
            stock_code=stock_code,
            stock_name="贵州茅台",
            exchange="SSE",
            board="main",
            added_at=now,
            effective_from=date(2026, 8, 13),
        )
        self.items.append(item)
        return item

    async def remove(self, user_id: uuid.UUID, stock_code: str, *, now: datetime) -> bool:
        self.user_ids.append(user_id)
        before = len(self.items)
        self.items = [item for item in self.items if item.stock_code != stock_code]
        return len(self.items) != before

    async def list_daily(self, user_id: uuid.UUID, week_id: date) -> list[object]:
        self.user_ids.append(user_id)
        assert week_id == WEEK_ID
        return []

    async def list_weekly(self, user_id: uuid.UUID, week_id: date) -> None:
        self.user_ids.append(user_id)
        assert week_id == WEEK_ID
        return None


def _override(fake: FakeWatchlistApplication) -> None:
    app.dependency_overrides[get_current_principal] = lambda: VIEWER
    app.dependency_overrides[require_csrf] = lambda: VIEWER
    app.dependency_overrides[get_watchlist_application] = lambda: fake


def test_viewer_can_manage_only_their_own_watchlist() -> None:
    fake = FakeWatchlistApplication()
    _override(fake)
    client = TestClient(app)
    try:
        search = client.get("/api/v1/stocks/search", params={"q": "茅台"})
        assert search.status_code == 200
        assert search.json()[0]["stock_code"] == "600519"

        added = client.post("/api/v1/me/watchlist", json={"stock_code": "600519"})
        assert added.status_code == 201
        assert added.json()["effective_from"] == "2026-08-13"

        listed = client.get("/api/v1/me/watchlist")
        assert listed.status_code == 200
        assert [item["stock_code"] for item in listed.json()] == ["600519"]

        removed = client.delete("/api/v1/me/watchlist/600519")
        assert removed.status_code == 204
        assert fake.user_ids and set(fake.user_ids) == {USER_ID}
        assert OTHER_USER_ID not in fake.user_ids
    finally:
        app.dependency_overrides.clear()


def test_watchlist_limit_error_is_safe_and_history_is_user_scoped() -> None:
    fake = FakeWatchlistApplication()
    fake.items = [
        WatchlistItemResponse(
            id=str(uuid.uuid4()),
            stock_code=f"{index:06d}",
            stock_name=f"样本{index}",
            exchange="SSE",
            board="main",
            added_at=datetime.now(UTC),
            effective_from=WEEK_ID,
        )
        for index in range(1, 6)
    ]
    _override(fake)
    client = TestClient(app)
    try:
        full = client.post("/api/v1/me/watchlist", json={"stock_code": "600519"})
        assert full.status_code == 422
        assert full.json()["detail"] == "每位用户最多关注5只标的"

        briefs = client.get(f"/api/v1/me/watchlist/weeks/{WEEK_ID}/briefs")
        assert briefs.status_code == 200
        assert briefs.json() == []

        review = client.get(f"/api/v1/me/watchlist/weeks/{WEEK_ID}/review")
        assert review.status_code == 404
        assert set(fake.user_ids) == {USER_ID}
    finally:
        app.dependency_overrides.clear()
