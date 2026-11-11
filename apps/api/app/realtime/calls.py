"""Who is in which call right now, kept per process (correct while the API runs as a single ASGI
worker, like presence and the rate limits). Postgres decides who MAY be in a room; this records
who IS, so signals are relayed only between sockets currently in the same room.

Changes happen while the caller holds the room row's FOR UPDATE lock, so a join and the last
leave of the same room can't interleave."""

import uuid


class CallRegistry:
    """room -> {student -> socket id}, in join order, plus each socket's rooms. A student is in a
    room through exactly one socket."""

    def __init__(self) -> None:
        self._rooms: dict[uuid.UUID, dict[uuid.UUID, str]] = {}
        self._by_sid: dict[str, set[uuid.UUID]] = {}

    def participants(self, room_id: uuid.UUID) -> list[uuid.UUID]:
        """Students in the room, earliest joiner first."""
        return list(self._rooms.get(room_id, {}))

    def count(self, room_id: uuid.UUID) -> int:
        return len(self._rooms.get(room_id, {}))

    def sid_of(self, room_id: uuid.UUID, user_id: uuid.UUID) -> str | None:
        return self._rooms.get(room_id, {}).get(user_id)

    def add(self, room_id: uuid.UUID, user_id: uuid.UUID, sid: str) -> None:
        room = self._rooms.setdefault(room_id, {})
        if user_id in room:
            raise ValueError("already in this room")
        room[user_id] = sid
        self._by_sid.setdefault(sid, set()).add(room_id)

    def remove(self, room_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Takes the student out; True when that left the room empty."""
        room = self._rooms.get(room_id)
        if room is None or user_id not in room:
            return False
        sid = room.pop(user_id)
        rooms = self._by_sid.get(sid)
        if rooms is not None:
            rooms.discard(room_id)
            if not rooms:
                del self._by_sid[sid]
        if room:
            return False
        del self._rooms[room_id]
        return True

    def rooms_of(self, sid: str) -> set[uuid.UUID]:
        """The rooms a socket is in (a copy, safe to iterate while leaving them)."""
        return set(self._by_sid.get(sid, ()))
