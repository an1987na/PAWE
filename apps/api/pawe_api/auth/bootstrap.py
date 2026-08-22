import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from pawe_api.auth.repository import normalize_username
from pawe_api.auth.security import PasswordManager
from pawe_api.config import get_settings
from pawe_api.db.models import AuthEvent, User
from pawe_api.db.session import SessionFactory


async def bootstrap_admin(username: str, password: str) -> bool:
    normalized = normalize_username(username)
    if len(normalized) < 3:
        raise ValueError("Bootstrap admin username must contain at least 3 characters")
    if len(password) < 7:
        raise ValueError("Bootstrap admin password must contain at least 7 characters")
    async with SessionFactory() as session:
        existing = await session.scalar(select(User).where(User.username == normalized))
        if existing is not None:
            if existing.role != "admin":
                raise ValueError("Bootstrap username already belongs to a non-admin user")
            return False
        now = datetime.now(UTC)
        user = User(
            id=uuid.uuid4(),
            username=normalized,
            password_hash=PasswordManager().hash(password),
            role="admin",
            is_active=True,
            created_by_user_id=None,
            created_at=now,
            password_changed_at=now,
            last_login_at=None,
        )
        session.add(user)
        await session.flush()
        session.add(
            AuthEvent(
                id=uuid.uuid4(),
                user_id=user.id,
                username=normalized,
                event_type="admin_bootstrapped",
                created_at=now,
                details={},
            )
        )
        await session.commit()
        return True


async def main() -> None:
    settings = get_settings()
    username = settings.bootstrap_admin_username
    password = settings.bootstrap_admin_password or ""
    if not password:
        raise SystemExit("PAWE_BOOTSTRAP_ADMIN_PASSWORD is required")
    created = await bootstrap_admin(username, password)
    print("Administrator created." if created else "Administrator already exists.")


if __name__ == "__main__":
    asyncio.run(main())
