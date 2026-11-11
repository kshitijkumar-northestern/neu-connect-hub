"""Socket.IO events for calls: room.join, rtc.signal and room.leave.

Every event re-checks the session it was opened with, exactly as chat.py does, so a socket never
outlives its session. A handler never raises to the socket layer: domain refusals come back as
AppError and are turned into acks, and anything truly unexpected is logged and answered the same
way. SDP and ICE candidates are relayed untouched and never logged.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import socketio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import INTERNAL_ERROR_MESSAGE, AppError
from app.core.rate_limit import Limit, RateLimiter
from app.db.session import Database
from app.modules.auth.sessions import load_session_by_hash
from app.modules.rooms.access import room_for_member
from app.modules.rooms.attendance import Joined, join_room, leave_room, open_rooms_for
from app.modules.rooms.signals import RoomPayload, RtcSignalPayload
from app.realtime.calls import CallRegistry

JOINS_PER_USER = Limit(max_events=30, window_seconds=60)
SIGNALS_PER_USER = Limit(max_events=600, window_seconds=60)
ACK_GRACE_SECONDS = 0.1

logger = logging.getLogger("app.rooms")


@dataclass(frozen=True)
class RoomHooks:
    """`left` is called by the server's disconnect handler, before chat presence's own.
    `recheck_room_access` is exposed on the HTTP app state for the block route to call once a
    block (or a team removal, which is also done through blocking) has committed."""

    left: Callable[[str], Awaitable[None]]
    recheck_room_access: Callable[[list[uuid.UUID]], Awaitable[None]]


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, **extra}}


def _fields(exc: ValidationError) -> list[dict[str, str]]:
    # Locations and messages only; nothing submitted (which may carry an SDP body) is echoed.
    return [
        {"location": ".".join(str(part) for part in err["loc"]), "message": err["msg"]}
        for err in exc.errors()
    ]


def _peer_out(user_id: uuid.UUID, display_name: str | None) -> dict[str, Any]:
    return {"user_id": str(user_id), "display_name": display_name}


def register_rooms(
    sio: socketio.AsyncServer,
    settings: Settings,
    database: Database,
    limiter: RateLimiter,
    calls: CallRegistry,
) -> RoomHooks:
    async def disconnect_after_ack(sid: str) -> None:
        await sio.sleep(ACK_GRACE_SECONDS)
        await sio.disconnect(sid)

    async def current_user(sid: str, db: AsyncSession) -> uuid.UUID | None:
        saved = await sio.get_session(sid)
        current = await load_session_by_hash(db, settings, saved["session_hash"])
        if current is None:
            # Disconnect just after the ack goes out, so the client learns why.
            sio.start_background_task(disconnect_after_ack, sid)
            return None
        return current.user.id

    async def notify_peer_left(room_id: uuid.UUID, user_id: uuid.UUID) -> None:
        remaining = await _leave(room_id, user_id)
        for target_sid in remaining:
            await sio.emit(
                "room.peer_left",
                {"room_id": str(room_id), "user_id": str(user_id)},
                to=target_sid,
            )

    async def _leave(room_id: uuid.UUID, user_id: uuid.UUID) -> list[str]:
        async with database.sessionmaker() as db:
            return await leave_room(db, calls, room_id, user_id)

    async def _force_leave(room_id: uuid.UUID, user_id: uuid.UUID, reason: str) -> None:
        """Leave on the student's behalf and tell both sides why: their own socket (if still
        registered) gets `room.removed`, the others get `room.peer_left`."""
        removed_sid = calls.sid_of(room_id, user_id)
        remaining = await _leave(room_id, user_id)
        if removed_sid is not None:
            await sio.emit(
                "room.removed", {"room_id": str(room_id), "reason": reason}, to=removed_sid
            )
        for target_sid in remaining:
            await sio.emit(
                "room.peer_left",
                {"room_id": str(room_id), "user_id": str(user_id)},
                to=target_sid,
            )

    async def recheck_room_access(user_ids: list[uuid.UUID]) -> None:
        """Called after a block (or a team removal, done through blocking) commits: media would
        otherwise keep flowing until the next signal, which may never come."""
        async with database.sessionmaker() as db:
            pairs = await open_rooms_for(db, user_ids)
        for room_id, user_id in pairs:
            async with database.sessionmaker() as db:
                room = await room_for_member(db, user_id, room_id)
            if room is None or room.room_state != "active":
                await _force_leave(room_id, user_id, "access_lost")

    async def join(sid: str, data: object = None) -> dict[str, Any]:
        async with database.sessionmaker() as db:
            user_id = await current_user(sid, db)
            if user_id is None:
                return _error("unauthorized", "Sign in to continue.")
            if limiter.hit("room-join", str(user_id), JOINS_PER_USER) is not None:
                return _error("rate_limited", "Too many attempts. Wait a moment and try again.")
            try:
                payload = RoomPayload.model_validate(data)
            except ValidationError as exc:
                return _error("validation_error", "That request isn't valid.", fields=_fields(exc))

            try:
                joined: Joined = await join_room(
                    db,
                    calls,
                    user_id=user_id,
                    room_id=payload.room_id,
                    sid=sid,
                    capacity=settings.room_capacity,
                )
            except AppError as exc:
                return _error(exc.code, exc.message)
            except Exception:
                logger.exception("room.join failed unexpectedly")
                return _error("internal_error", INTERNAL_ERROR_MESSAGE)

        for peer_id in calls.participants(payload.room_id):
            if peer_id == user_id:
                continue
            target_sid = calls.sid_of(payload.room_id, peer_id)
            if target_sid is None:
                continue
            if joined.took_over_sid is not None:
                # They dropped the old connection and should wait for a fresh offer.
                await sio.emit(
                    "room.peer_left",
                    {"room_id": str(payload.room_id), "user_id": str(user_id)},
                    to=target_sid,
                )
            await sio.emit(
                "room.peer_joined",
                {
                    "room_id": str(payload.room_id),
                    "peer": _peer_out(user_id, joined.display_name),
                },
                to=target_sid,
            )
        if joined.took_over_sid is not None:
            await sio.emit(
                "room.removed",
                {"room_id": str(payload.room_id), "reason": "joined_elsewhere"},
                to=joined.took_over_sid,
            )
        return {
            "ok": True,
            "room_id": str(payload.room_id),
            "peers": [_peer_out(p.user_id, p.display_name) for p in joined.peers],
        }

    async def signal(sid: str, data: object = None) -> dict[str, Any]:
        async with database.sessionmaker() as db:
            user_id = await current_user(sid, db)
            if user_id is None:
                return _error("unauthorized", "Sign in to continue.")
            if limiter.hit("rtc-signal", str(user_id), SIGNALS_PER_USER) is not None:
                return _error("rate_limited", "Too many attempts. Wait a moment and try again.")
            try:
                payload = RtcSignalPayload.model_validate(data)
            except ValidationError as exc:
                return _error("validation_error", "That request isn't valid.", fields=_fields(exc))

            if calls.sid_of(payload.room_id, user_id) != sid:
                return _error("not_in_room", "You're not in this call.")

            room = await room_for_member(db, user_id, payload.room_id)
            lost_access = room is None or room.room_state != "active"
            target_sid = None
            if not lost_access:
                target_sid = calls.sid_of(payload.room_id, payload.to)
                if payload.to == user_id or target_sid is None:
                    return _error("peer_not_found", "That student isn't in this call.")

        if lost_access:
            # They lost access mid-call (a block, or a team removal): leave on their behalf.
            await notify_peer_left(payload.room_id, user_id)
            return _error("not_found", "This call isn't available.")

        await sio.emit(
            "rtc.signal",
            {
                "room_id": str(payload.room_id),
                "from": str(user_id),
                "signal": payload.signal.model_dump(mode="json"),
            },
            to=target_sid,
        )
        return {"ok": True}

    async def leave(sid: str, data: object = None) -> dict[str, Any]:
        async with database.sessionmaker() as db:
            user_id = await current_user(sid, db)
            if user_id is None:
                return _error("unauthorized", "Sign in to continue.")
            try:
                payload = RoomPayload.model_validate(data)
            except ValidationError as exc:
                return _error("validation_error", "That request isn't valid.", fields=_fields(exc))
        # Only the socket actually registered for this room may evict it: a stale tab of the
        # same student (one that lost the network and never saw a disconnect) must never evict
        # the tab that's really in the call.
        if calls.sid_of(payload.room_id, user_id) == sid:
            await notify_peer_left(payload.room_id, user_id)
        return {"ok": True}

    async def left(sid: str) -> None:
        rooms = calls.rooms_of(sid)
        if not rooms:
            return
        saved = await sio.get_session(sid)
        if not saved:
            return
        user_id = uuid.UUID(saved["user_id"])
        for room_id in rooms:
            await notify_peer_left(room_id, user_id)

    sio.on("room.join", join)
    sio.on("rtc.signal", signal)
    sio.on("room.leave", leave)
    return RoomHooks(left=left, recheck_room_access=recheck_room_access)
