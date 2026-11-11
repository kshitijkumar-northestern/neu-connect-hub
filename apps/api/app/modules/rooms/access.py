"""Who may use a room: an active member of its conversation. HTTP routes and every socket event go
through here, so a block or a removal from a team (both deactivate the conversation member) ends
call access the moment it commits."""

import uuid

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.chat.models import ConversationMember
from app.modules.rooms.models import Room


async def room_for_member(
    db: AsyncSession, user_id: uuid.UUID, room_id: uuid.UUID, *, lock: bool = False
) -> Room | None:
    """The room, in any state, if the student may use it; None when it's missing or they may not
    (callers answer both with the same not-found, so a room's existence never leaks).
    `lock=True` takes FOR UPDATE on the room row, which serialises joins and leaves."""
    query = select(Room).where(
        Room.id == room_id,
        exists().where(
            ConversationMember.conversation_id == Room.conversation_id,
            ConversationMember.user_id == user_id,
            ConversationMember.active,
        ),
    )
    if lock:
        query = query.with_for_update(of=Room)
    return await db.scalar(query)


async def is_conversation_member(
    db: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> bool:
    active = await db.scalar(
        select(ConversationMember.active).where(
            ConversationMember.conversation_id == conversation_id,
            ConversationMember.user_id == user_id,
        )
    )
    return bool(active)
