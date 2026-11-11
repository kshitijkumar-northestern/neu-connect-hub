import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoomCreate(_Request):
    conversation_id: uuid.UUID


class ParticipantOut(BaseModel):
    user_id: uuid.UUID
    display_name: str | None


class RoomOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    kind: Literal["direct", "project"]
    name: str | None
    project_id: uuid.UUID | None
    room_state: Literal["active", "ended"]
    capacity: int
    participants: list[ParticipantOut]
    created_at: datetime
    ended_at: datetime | None


class IceServersOut(BaseModel):
    """`ice_servers` entries are plain dicts (not a shared schema) because a STUN entry carries
    only `urls`, while a TURN entry also carries `username` and `credential`: omitted keys, not
    null values, so the web client's optional fields line up."""

    ice_servers: list[dict[str, Any]]
    expires_at: datetime | None
