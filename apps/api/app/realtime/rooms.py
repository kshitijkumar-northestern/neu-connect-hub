"""Socket.IO room names. Every socket joins its session's room and its student's room."""

import uuid


def session_room(session_hash: str) -> str:
    return f"session:{session_hash}"


def user_room(user_id: uuid.UUID) -> str:
    return f"user:{user_id}"
