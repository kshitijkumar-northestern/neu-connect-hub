import logging
from http.cookies import CookieError, SimpleCookie
from typing import Any

import socketio

from app.core.config import Settings
from app.core.rate_limit import RateLimiter
from app.core.security import SESSION_COOKIE
from app.db.session import Database
from app.modules.auth.sessions import load_session
from app.realtime.calls import CallRegistry
from app.realtime.chat import register_chat
from app.realtime.presence import Presence
from app.realtime.room_events import RoomHooks, register_rooms
from app.realtime.rooms import session_room, user_room

logger = logging.getLogger("app.realtime")


def _session_cookie(cookie_header: str) -> str | None:
    try:
        morsel = SimpleCookie(cookie_header).get(SESSION_COOKIE)
    except CookieError:
        return None
    return morsel.value if morsel is not None else None


async def disconnect_room(sio: socketio.AsyncServer, room: str) -> None:
    """Disconnect every socket in a room (this process only, so it assumes a single worker)."""
    for sid, _ in list(sio.manager.get_participants("/", room)):
        await sio.disconnect(sid)


def create_socketio_server(
    settings: Settings,
    database: Database,
    limiter: RateLimiter,
    presence: Presence,
    calls: CallRegistry,
) -> tuple[socketio.AsyncServer, RoomHooks]:
    """Socket.IO server that accepts only signed-in browsers from the web origin. Also returns
    the rooms hooks, so the caller can expose `recheck_room_access` on the HTTP app state."""
    sio = socketio.AsyncServer(
        async_mode="asgi",
        cors_allowed_origins=[settings.web_origin],
        logger=False,
        engineio_logger=False,
    )

    async def connect(sid: str, environ: dict[str, Any], auth: object = None) -> None:
        raw = _session_cookie(environ.get("HTTP_COOKIE", ""))
        current = None
        if raw:
            async with database.sessionmaker() as db:
                current = await load_session(db, settings, raw)
        if current is None:
            raise socketio.exceptions.ConnectionRefusedError("unauthorized")
        await sio.save_session(
            sid, {"user_id": str(current.user.id), "session_hash": current.session.id_hash}
        )
        await sio.enter_room(sid, session_room(current.session.id_hash))
        await sio.enter_room(sid, user_room(current.user.id))
        await hooks.joined(current.user.id)

    async def disconnect(sid: str, reason: object = None) -> None:
        # Chat presence must always run, even if leaving a call fails (e.g. a database error):
        # it must never leak a now-disconnected student as still online.
        try:
            await rooms_hooks.left(sid)
        except Exception:
            logger.exception("Leaving a call failed on disconnect")
        finally:
            await hooks.left(sid)

    hooks = register_chat(sio, settings, database, limiter, presence)
    rooms_hooks = register_rooms(sio, settings, database, limiter, calls)
    sio.on("connect", connect)
    sio.on("disconnect", disconnect)
    return sio, rooms_hooks


__all__ = ["create_socketio_server", "disconnect_room", "session_room", "user_room"]
