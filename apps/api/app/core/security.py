"""Request authentication: trusted origin, session cookie and CSRF token."""

import hmac

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.db.session import get_session
from app.modules.auth.models import UserSession
from app.modules.auth.sessions import CurrentSession, load_session
from app.modules.auth.tokens import hash_token

SESSION_COOKIE = "nu_session"
# Readable by the web app, which echoes it in CSRF_HEADER (see apps/web/src/lib/http.ts).
CSRF_COOKIE = "nu_csrf"
CSRF_HEADER = "X-CSRF-Token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


async def require_trusted_origin(request: Request) -> None:
    """Unsafe requests must come from the web app's origin (browsers always send Origin)."""
    if request.method not in UNSAFE_METHODS:
        return
    settings: Settings = request.app.state.settings
    if request.headers.get("origin") != settings.web_origin:
        raise AppError(
            403, "origin_not_allowed", "This request must come from the NU Connect Hub site."
        )


def verify_csrf(request: Request, session: UserSession) -> None:
    """The header must match the CSRF cookie and the token bound to this session."""
    header = request.headers.get(CSRF_HEADER, "").encode()
    cookie = request.cookies.get(CSRF_COOKIE, "").encode()
    if (
        not header
        or not hmac.compare_digest(header, cookie)
        or not hmac.compare_digest(hash_token(header.decode()).encode(), session.csrf_hash.encode())
    ):
        raise AppError(
            403, "csrf_failed", "Your session check failed. Refresh the page and try again."
        )


async def optional_session(
    request: Request, db: AsyncSession = Depends(get_session)
) -> CurrentSession | None:
    """The signed-in session, or None. Performs no CSRF check."""
    raw = request.cookies.get(SESSION_COOKIE)
    return await load_session(db, request.app.state.settings, raw) if raw else None


async def current_session(
    request: Request, current: CurrentSession | None = Depends(optional_session)
) -> CurrentSession:
    """Requires a session; unsafe methods must also carry the matching CSRF token."""
    if current is None:
        raise AppError(401, "unauthorized", "Sign in to continue.")
    if request.method in UNSAFE_METHODS:
        verify_csrf(request, current.session)
    return current


async def require_verified(current: CurrentSession = Depends(current_session)) -> CurrentSession:
    if current.user.verified_at is None:
        raise AppError(403, "verification_required", "Verify your email address to continue.")
    return current


def set_session_cookies(response: Response, settings: Settings, session: str, csrf: str) -> None:
    common = {
        "max_age": settings.session_absolute_lifetime_seconds,
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "path": "/",
        "domain": settings.cookie_domain,
    }
    response.set_cookie(SESSION_COOKIE, session, httponly=True, **common)  # type: ignore[arg-type]
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, **common)  # type: ignore[arg-type]


def clear_session_cookies(response: Response, settings: Settings) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(
            name,
            path="/",
            domain=settings.cookie_domain,
            secure=settings.cookie_secure,
            samesite="lax",
        )
