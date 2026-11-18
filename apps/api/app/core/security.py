"""Request authentication: trusted origin, session cookie and CSRF token."""

import hmac
from datetime import UTC, datetime, timedelta

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.types import Message, Receive, Scope, Send

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
# What the web app sends cross-origin when it runs on a sibling host of the API.
CORS_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
CORS_HEADERS = ("Content-Type", CSRF_HEADER)


class WebOriginCORSMiddleware(CORSMiddleware):
    """CORS for the web origin alone: its preflights are answered and its responses may be read
    with credentials. A request from any other origin passes through with no CORS headers at
    all (Starlette would still advertise the allowed methods and credentials to it), so its
    preflight fails in the browser and the Origin and CSRF checks refuse its unsafe requests.

    Configure it with an explicit list of origins only: never "*" and never a pattern.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            origin = Headers(scope=scope).get("origin")
            if origin is not None and not self.is_allowed_origin(origin):
                await self.app(scope, receive, _vary_on_origin(send))
                return
        await super().__call__(scope, receive, send)


def _vary_on_origin(send: Send) -> Send:
    """The answer differs by Origin, so no cache may hand it to another origin."""

    async def send_varying(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = MutableHeaders(scope=message)
            if "origin" not in headers.get("vary", "").lower():
                headers.add_vary_header("Origin")
        await send(message)

    return send_varying


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


async def require_admin(current: CurrentSession = Depends(require_verified)) -> CurrentSession:
    """Administrators only. Everyone else gets the same 404 as a missing page, so the admin area
    doesn't reveal that it exists."""
    if current.user.role != "admin":
        raise AppError(404, "not_found", "Not found.")
    return current


def mfa_is_recent(session: UserSession, settings: Settings) -> bool:
    """True when the session passed MFA within ADMIN_MFA_MAX_AGE_SECONDS. The one definition
    both the admin routes and the MFA status use, so the two can never disagree."""
    verified_at = session.mfa_verified_at
    max_age = timedelta(seconds=settings.admin_mfa_max_age_seconds)
    return verified_at is not None and datetime.now(UTC) - verified_at <= max_age


async def require_admin_mfa(
    request: Request, current: CurrentSession = Depends(require_admin)
) -> CurrentSession:
    """An administrator whose session passed MFA recently."""
    if not mfa_is_recent(current.session, request.app.state.settings):
        raise AppError(403, "mfa_required", "Confirm it's you with your authenticator app.")
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
