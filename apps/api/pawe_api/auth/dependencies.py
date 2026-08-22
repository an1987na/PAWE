import hmac
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from pawe_api.auth.repository import AuthApplication, Principal, SqlAuthApplication
from pawe_api.auth.security import secret_hash
from pawe_api.db.session import get_db_session

SESSION_COOKIE = "pawe_session"
CSRF_COOKIE = "pawe_csrf"


def get_auth_application(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthApplication:
    return SqlAuthApplication(session)


AuthApplicationDependency = Annotated[AuthApplication, Depends(get_auth_application)]


async def get_current_principal(
    auth: AuthApplicationDependency,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> Principal:
    if not session_token:
        raise HTTPException(status_code=401, detail="Authentication required")
    principal = await auth.resolve(session_token)
    if principal is None:
        raise HTTPException(status_code=401, detail="Session is invalid or expired")
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


def require_admin(principal: CurrentPrincipal) -> Principal:
    if principal.user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required")
    return principal


AdminPrincipal = Annotated[Principal, Depends(require_admin)]


def require_csrf(
    principal: CurrentPrincipal,
    csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE)] = None,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> Principal:
    if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    if not hmac.compare_digest(secret_hash(csrf_header), principal.csrf_token_hash):
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    return principal


CsrfPrincipal = Annotated[Principal, Depends(require_csrf)]


def require_admin_csrf(principal: CsrfPrincipal) -> Principal:
    return require_admin(principal)


AdminCsrfPrincipal = Annotated[Principal, Depends(require_admin_csrf)]
