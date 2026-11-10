import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.modules.chat.models import MESSAGE_MAX
from app.modules.profiles.schemas import MultiLine

MAX_PAGE = 100
DEFAULT_PAGE = 50


def _strip(value: object) -> object:
    return value.replace("\r\n", "\n").strip() if isinstance(value, str) else value


MessageBody = Annotated[
    str, BeforeValidator(_strip), Field(min_length=1, max_length=MESSAGE_MAX), MultiLine
]


class ChatSend(BaseModel):
    """The payload of a chat.send socket event."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: uuid.UUID
    client_msg_id: uuid.UUID
    body: MessageBody


class MessageOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    author_id: uuid.UUID | None
    body: str | None
    client_msg_id: uuid.UUID | None
    created_at: datetime
    removed: bool


class MessagePage(BaseModel):
    """Messages oldest to newest. `before` pages further back (None when nothing is older);
    `after` is the newest cursor, for catching up after a reconnect."""

    messages: list[MessageOut]
    before: str | None
    after: str | None


class OtherMember(BaseModel):
    user_id: uuid.UUID
    display_name: str | None
    online: bool


class ConversationOut(BaseModel):
    id: uuid.UUID
    kind: Literal["direct"]
    other: OtherMember
    last_message: MessageOut | None
    updated_at: datetime
