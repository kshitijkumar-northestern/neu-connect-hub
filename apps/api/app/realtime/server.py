import uuid
from http.cookies import CookieError, SimpleCookie
from typing import Any

import socketio

from app.core.config import Settings
from app.core.security import SESSION_COOKIE
from app.db.session import Database
from app.modules.auth.sessions import load_session


def session_room(session_hash: str) -> str:
    return f"session:{session_hash}"


def user_room(user_id: uuid.UUID) -> str:
    return f"user:{user_id}"


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


def create_socketio_server(settings: Settings, database: Database) -> socketio.AsyncServer:
    """Socket.IO server that accepts only signed-in browsers from the web origin."""
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

    sio.on("connect", connect)
    return sio
