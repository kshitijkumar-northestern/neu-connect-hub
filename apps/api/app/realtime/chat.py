"""Socket.IO chat events: chat.send in, chat.message and presence.changed out.

Every event re-checks the session it was opened with, so a socket never outlives its session,
and nothing here logs a message body.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import socketio
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limit import Limit, RateLimiter
from app.db.session import Database
from app.modules.auth.sessions import load_session_by_hash
from app.modules.chat.schemas import ChatSend
from app.modules.chat.service import ChatService
from app.realtime.presence import Presence
from app.realtime.rooms import user_room

SENDS_PER_USER = Limit(max_events=60, window_seconds=60)
ACK_GRACE_SECONDS = 0.1


@dataclass(frozen=True)
class PresenceHooks:
    """Called by the connect and disconnect handlers."""

    joined: Callable[[uuid.UUID], Awaitable[None]]
    left: Callable[[str], Awaitable[None]]


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, **extra}}


def register_chat(
    sio: socketio.AsyncServer,
    settings: Settings,
    database: Database,
    limiter: RateLimiter,
    presence: Presence,
) -> PresenceHooks:
    async def announce(user_id: uuid.UUID, online: bool) -> None:
        async with database.sessionmaker() as db:
            partners = await ChatService(db).partners(user_id)
        for partner in partners:
            await sio.emit(
                "presence.changed",
                {"user_id": str(user_id), "online": online},
                room=user_room(partner),
            )

    async def joined(user_id: uuid.UUID) -> None:
        if presence.joined(user_id):
            await announce(user_id, online=True)

    async def left(sid: str) -> None:
        saved = await sio.get_session(sid)
        if saved and presence.left(uuid.UUID(saved["user_id"])):
            await announce(uuid.UUID(saved["user_id"]), online=False)

    async def disconnect_after_ack(sid: str) -> None:
        await sio.sleep(ACK_GRACE_SECONDS)
        await sio.disconnect(sid)

    async def send(sid: str, data: object = None) -> dict[str, Any]:
        saved = await sio.get_session(sid)
        async with database.sessionmaker() as db:
            current = await load_session_by_hash(db, settings, saved["session_hash"])
            if current is None:
                # Disconnect just after the ack goes out, so the client learns why.
                sio.start_background_task(disconnect_after_ack, sid)
                return _error("unauthorized", "Sign in to continue.")
            user_id = current.user.id
            if limiter.hit("chat-send", str(user_id), SENDS_PER_USER) is not None:
                return _error("rate_limited", "You're sending messages too quickly.")
            try:
                payload = ChatSend.model_validate(data)
            except ValidationError as exc:
                # Locations and messages only; submitted values (the body) are never echoed.
                fields = [
                    {"location": ".".join(str(part) for part in err["loc"]), "message": err["msg"]}
                    for err in exc.errors()
                ]
                return _error("validation_error", "The message is invalid.", fields=fields)
            try:
                message, members = await ChatService(db).send(
                    user_id, payload.conversation_id, payload.client_msg_id, payload.body
                )
            except AppError as exc:
                return _error(exc.code, exc.message)

        body = message.model_dump(mode="json")
        for member in members:
            await sio.emit("chat.message", body, room=user_room(member))
        return {"ok": True, "message": body}

    sio.on("chat.send", send)
    return PresenceHooks(joined=joined, left=left)
