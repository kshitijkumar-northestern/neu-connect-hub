"""The database side of a call's attendance: joining and leaving. Both hold the room row's
FOR UPDATE lock across the moment they change `app.state.calls` (the in-memory registry) and the
attendance log, so a join can never slip in while the last leave is ending the room, and the two
never interleave inconsistently."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.core.errors import AppError
from app.modules.profiles.models import Profile
from app.modules.rooms.access import room_for_member
from app.modules.rooms.models import Room, RoomMember
from app.realtime.calls import CallRegistry

NOT_FOUND_MESSAGE = "This call isn't available."


@dataclass(frozen=True)
class Peer:
    user_id: uuid.UUID
    display_name: str | None


@dataclass(frozen=True)
class Joined:
    """The result of a successful join: who was already there (for the ack), the joiner's own
    name (for the room.peer_joined broadcast), and the sid of a prior socket of theirs that just
    got taken over, if any."""

    peers: list[Peer]
    display_name: str | None
    took_over_sid: str | None = None


async def _display_names(
    db: AsyncSession, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str | None]:
    if not user_ids:
        return {}
    rows = await db.execute(
        select(Profile.user_id, Profile.display_name).where(Profile.user_id.in_(user_ids))
    )
    found = dict(rows.all())
    return {user_id: found.get(user_id) for user_id in user_ids}


async def _close_open_attendance(db: AsyncSession, room_id: uuid.UUID, user_id: uuid.UUID) -> None:
    await db.execute(
        update(RoomMember)
        .where(
            RoomMember.room_id == room_id,
            RoomMember.user_id == user_id,
            RoomMember.left_at.is_(None),
        )
        .values(left_at=func.now())
    )


async def join_room(
    db: AsyncSession,
    calls: CallRegistry,
    *,
    user_id: uuid.UUID,
    room_id: uuid.UUID,
    sid: str,
    capacity: int,
) -> Joined:
    """One transaction holding the room row lock across the registry check-and-add and the
    attendance insert. Raises AppError for every refusal; never raises for an ordinary one."""
    room = await room_for_member(db, user_id, room_id, lock=True)
    if room is None:
        raise AppError(404, "not_found", NOT_FOUND_MESSAGE)
    if room.room_state == "ended":
        raise AppError(409, "room_ended", "This call has ended.")

    # Already in the room through another socket: that socket takes over (capacity doesn't
    # change, since nobody new is being added). A dropped network leaves the old socket
    # registered until the server notices, so this is also what makes Rejoin work at once.
    old_sid = calls.sid_of(room_id, user_id)
    if old_sid is None and calls.count(room_id) >= capacity:
        raise AppError(409, "room_full", "This call is full.")

    already_there = [peer_id for peer_id in calls.participants(room_id) if peer_id != user_id]
    names = await _display_names(db, [*already_there, user_id])
    peers = [Peer(user_id=peer_id, display_name=names.get(peer_id)) for peer_id in already_there]

    if old_sid is not None:
        calls.remove(room_id, user_id)
    # A server restart empties the registry but leaves the row open; close it before the new
    # one (also the old row of a takeover), or the partial unique index on (room, user, present)
    # refuses the insert.
    await _close_open_attendance(db, room_id, user_id)
    db.add(RoomMember(room_id=room_id, user_id=user_id))
    calls.add(room_id, user_id, sid)
    try:
        await db.commit()
    except Exception:
        calls.remove(room_id, user_id)
        if old_sid is not None:
            calls.add(room_id, user_id, old_sid)
        await db.rollback()
        raise
    return Joined(peers=peers, display_name=names.get(user_id), took_over_sid=old_sid)


async def open_rooms_for(
    db: AsyncSession, user_ids: list[uuid.UUID]
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """(room_id, user_id) pairs with an open attendance row for these students: the rooms they
    are currently registered in, read straight from the durable log rather than the in-process
    registry, so a hook outside the socket layer (e.g. the block route) can find them too."""
    if not user_ids:
        return []
    rows = await db.execute(
        select(RoomMember.room_id, RoomMember.user_id).where(
            RoomMember.user_id.in_(user_ids), RoomMember.left_at.is_(None)
        )
    )
    return list(rows.all())


async def leave_room(
    db: AsyncSession, calls: CallRegistry, room_id: uuid.UUID, user_id: uuid.UUID
) -> list[str]:
    """The leave procedure: lock the room row by id (no membership filter, so a student who
    lost access can still leave), remove from the registry, close the attendance row, and end
    the room when that emptied it. Idempotent for a student who isn't currently registered.
    Returns the sids still in the room afterwards, to notify once this commits."""
    row = (
        await db.execute(
            select(Room.id, Room.room_state).where(Room.id == room_id).with_for_update()
        )
    ).first()
    emptied = calls.remove(room_id, user_id)
    remaining = [
        sid
        for sid in (calls.sid_of(room_id, peer_id) for peer_id in calls.participants(room_id))
        if sid is not None
    ]

    if row is not None:
        await _close_open_attendance(db, room_id, user_id)
        if emptied and row.room_state == "active":
            await db.execute(
                update(Room)
                .where(Room.id == room_id, Room.room_state == "active")
                .values(room_state="ended", ended_at=func.now())
            )
    await db.commit()
    return remaining
