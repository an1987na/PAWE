import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pawe_api.auth.security import PasswordManager, new_secret, secret_hash
from pawe_api.contracts import CreateUserRequest, UpdateUserRequest, UserResponse
from pawe_api.db.models import AuthEvent, User, UserSession

Role = Literal["admin", "viewer"]


@dataclass(frozen=True)
class Principal:
    user: UserResponse
    session_id: uuid.UUID
    csrf_token_hash: str


@dataclass(frozen=True)
class IssuedSession:
    user: UserResponse
    session_token: str
    csrf_token: str


class AuthApplication(Protocol):
    async def login(self, username: str, password: str, ttl_hours: int) -> IssuedSession | None: ...

    async def resolve(self, session_token: str) -> Principal | None: ...

    async def logout(self, session_id: uuid.UUID) -> None: ...

    async def list_users(self) -> list[UserResponse]: ...

    async def create_user(
        self, request: CreateUserRequest, created_by: uuid.UUID
    ) -> UserResponse: ...

    async def update_user(
        self, user_id: uuid.UUID, request: UpdateUserRequest, actor_id: uuid.UUID
    ) -> UserResponse | None: ...


class DuplicateUsernameError(ValueError):
    pass


class LastAdminError(ValueError):
    pass


def normalize_username(username: str) -> str:
    return username.strip().lower()


def to_user_response(user: User) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


class SqlAuthApplication:
    def __init__(self, session: AsyncSession, passwords: PasswordManager | None = None) -> None:
        self._session = session
        self._passwords = passwords or PasswordManager()

    async def login(self, username: str, password: str, ttl_hours: int) -> IssuedSession | None:
        normalized = normalize_username(username)
        user = await self._session.scalar(select(User).where(User.username == normalized))
        now = datetime.now(UTC)
        valid_password = user is not None and self._passwords.verify(user.password_hash, password)
        if user is None or not user.is_active or not valid_password:
            self._session.add(
                AuthEvent(
                    id=uuid.uuid4(),
                    user_id=user.id if user else None,
                    username=normalized,
                    event_type="login_failed",
                    created_at=now,
                    details={},
                )
            )
            await self._session.commit()
            return None
        if self._passwords.needs_rehash(user.password_hash):
            user.password_hash = self._passwords.hash(password)
            user.password_changed_at = now
        session_token = new_secret()
        csrf_token = new_secret()
        user.last_login_at = now
        self._session.add(
            UserSession(
                id=uuid.uuid4(),
                user_id=user.id,
                token_hash=secret_hash(session_token),
                csrf_token_hash=secret_hash(csrf_token),
                created_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(hours=ttl_hours),
                revoked_at=None,
            )
        )
        self._session.add(
            AuthEvent(
                id=uuid.uuid4(),
                user_id=user.id,
                username=user.username,
                event_type="login_success",
                created_at=now,
                details={},
            )
        )
        await self._session.commit()
        return IssuedSession(to_user_response(user), session_token, csrf_token)

    async def resolve(self, session_token: str) -> Principal | None:
        now = datetime.now(UTC)
        row = (
            await self._session.execute(
                select(UserSession, User)
                .join(User, User.id == UserSession.user_id)
                .where(UserSession.token_hash == secret_hash(session_token))
            )
        ).one_or_none()
        if row is None:
            return None
        session, user = row
        if session.revoked_at is not None or session.expires_at <= now or not user.is_active:
            return None
        session.last_seen_at = now
        await self._session.commit()
        return Principal(to_user_response(user), session.id, session.csrf_token_hash)

    async def logout(self, session_id: uuid.UUID) -> None:
        session = await self._session.get(UserSession, session_id)
        if session is not None and session.revoked_at is None:
            session.revoked_at = datetime.now(UTC)
            await self._session.commit()

    async def list_users(self) -> list[UserResponse]:
        users = (await self._session.scalars(select(User).order_by(User.created_at))).all()
        return [to_user_response(user) for user in users]

    async def create_user(
        self, request: CreateUserRequest, created_by: uuid.UUID
    ) -> UserResponse:
        username = normalize_username(request.username)
        if await self._session.scalar(select(User.id).where(User.username == username)):
            raise DuplicateUsernameError("Username already exists")
        now = datetime.now(UTC)
        user = User(
            id=uuid.uuid4(),
            username=username,
            password_hash=self._passwords.hash(request.password),
            role="viewer",
            is_active=True,
            created_by_user_id=created_by,
            created_at=now,
            password_changed_at=now,
            last_login_at=None,
        )
        self._session.add(user)
        await self._session.flush()
        self._session.add(
            AuthEvent(
                id=uuid.uuid4(),
                user_id=user.id,
                username=username,
                event_type="user_created",
                created_at=now,
                details={"created_by": str(created_by)},
            )
        )
        await self._session.commit()
        return to_user_response(user)

    async def update_user(
        self, user_id: uuid.UUID, request: UpdateUserRequest, actor_id: uuid.UUID
    ) -> UserResponse | None:
        user = await self._session.get(User, user_id)
        if user is None:
            return None
        if user.id == actor_id and not request.is_active:
            raise LastAdminError("Administrators cannot disable their own account")
        user.is_active = request.is_active
        now = datetime.now(UTC)
        if not request.is_active:
            sessions = (
                await self._session.scalars(
                    select(UserSession).where(
                        UserSession.user_id == user.id, UserSession.revoked_at.is_(None)
                    )
                )
            ).all()
            for session in sessions:
                session.revoked_at = now
        self._session.add(
            AuthEvent(
                id=uuid.uuid4(),
                user_id=user.id,
                username=user.username,
                event_type="user_enabled" if request.is_active else "user_disabled",
                created_at=now,
                details={"actor_id": str(actor_id)},
            )
        )
        await self._session.commit()
        return to_user_response(user)
