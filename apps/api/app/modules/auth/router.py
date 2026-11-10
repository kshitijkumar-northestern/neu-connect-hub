from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.mail import EmailMessage, deliver
from app.core.security import (
    SESSION_COOKIE,
    clear_session_cookies,
    current_session,
    optional_session,
    set_session_cookies,
)
from app.db.session import get_session
from app.modules.auth.schemas import (
    EmailRequest,
    LoginRequest,
    PasswordResetConfirm,
    RegisterRequest,
    StatusResponse,
    TokenRequest,
    UserResponse,
)
from app.modules.auth.service import AuthService
from app.modules.auth.sessions import CurrentSession
from app.realtime.server import disconnect_room, session_room, user_room

router = APIRouter(tags=["auth"])


def auth_service(request: Request, db: AsyncSession = Depends(get_session)) -> AuthService:
    state = request.app.state
    client_ip = request.client.host if request.client else "unknown"
    return AuthService(db, state.settings, state.passwords, state.limiter, client_ip)


def _send_later(
    request: Request, background: BackgroundTasks, message: EmailMessage | None
) -> None:
    """Email goes out after the response, so its timing can't reveal anything."""
    if message is not None:
        background.add_task(deliver, request.app.state.mailer, message)


async def _disconnect(request: Request, room: str) -> None:
    sio = getattr(request.app.state, "sio", None)
    if sio is not None:
        await disconnect_room(sio, room)


@router.post("/auth/register", status_code=202)
async def register(
    body: RegisterRequest,
    request: Request,
    background: BackgroundTasks,
    service: AuthService = Depends(auth_service),
) -> StatusResponse:
    _send_later(request, background, await service.register(body.email, body.password))
    return StatusResponse(status="verification_sent")


@router.post("/auth/resend-verification", status_code=202)
async def resend_verification(
    body: EmailRequest,
    request: Request,
    background: BackgroundTasks,
    service: AuthService = Depends(auth_service),
) -> StatusResponse:
    _send_later(request, background, await service.resend_verification(body.email))
    return StatusResponse(status="verification_sent")


@router.post("/auth/verify")
async def verify_email(
    body: TokenRequest, service: AuthService = Depends(auth_service)
) -> StatusResponse:
    await service.verify_email(body.token)
    return StatusResponse(status="verified")


@router.post("/auth/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    service: AuthService = Depends(auth_service),
) -> UserResponse:
    result = await service.login(body.email, body.password, request.cookies.get(SESSION_COOKIE))
    set_session_cookies(
        response, request.app.state.settings, result.session_cookie, result.csrf_cookie
    )
    if result.replaced_session_hash:
        await _disconnect(request, session_room(result.replaced_session_hash))
    return UserResponse.of(result.user)


@router.post("/auth/logout", status_code=204)
async def logout(
    request: Request,
    current: CurrentSession | None = Depends(optional_session),
    service: AuthService = Depends(auth_service),
) -> Response:
    """Always ends the session and clears the cookies. Like every unsafe request it must come
    from the web origin; it deliberately needs no CSRF token, so signing out never gets stuck."""
    if current is not None:
        session_hash = current.session.id_hash
        await service.end_session(current.session)
        await _disconnect(request, session_room(session_hash))
    response = Response(status_code=204)
    clear_session_cookies(response, request.app.state.settings)
    return response


@router.post("/auth/password-reset/request", status_code=202)
async def request_password_reset(
    body: EmailRequest,
    request: Request,
    background: BackgroundTasks,
    service: AuthService = Depends(auth_service),
) -> StatusResponse:
    _send_later(request, background, await service.request_password_reset(body.email))
    return StatusResponse(status="reset_requested")


@router.post("/auth/password-reset/confirm")
async def confirm_password_reset(
    body: PasswordResetConfirm, request: Request, service: AuthService = Depends(auth_service)
) -> StatusResponse:
    user_id = await service.confirm_password_reset(body.token, body.password)
    await _disconnect(request, user_room(user_id))
    return StatusResponse(status="password_reset")


@router.get("/me")
async def me(current: CurrentSession = Depends(current_session)) -> UserResponse:
    return UserResponse.of(current.user)
