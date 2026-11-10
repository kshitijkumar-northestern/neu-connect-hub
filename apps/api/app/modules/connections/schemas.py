import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID


class ConnectionOut(BaseModel):
    """A connection as the caller sees it: `user_id` and `display_name` are the other student."""

    id: uuid.UUID
    state: Literal["pending", "accepted"]
    direction: Literal["incoming", "outgoing"]
    user_id: uuid.UUID
    display_name: str | None
    created_at: datetime
    responded_at: datetime | None
    conversation_id: uuid.UUID | None
