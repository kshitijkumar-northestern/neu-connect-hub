import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from sqlalchemy import and_, func, select, tuple_
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import AppError
from app.modules.chat.models import Conversation, ConversationMember, Message
from app.modules.chat.schemas import (
    ConversationOut,
    MessageOut,
    MessagePage,
    OtherMember,
)
from app.modules.profiles.models import Profile
from app.modules.profiles.visibility import blocked_between
from app.modules.teams.models import Project
from app.realtime.presence import Presence

MAX_CONVERSATIONS = 200
NOT_FOUND = "Conversation not found."


@dataclass(frozen=True)
class Cursor:
    created_at: datetime
    id: uuid.UUID

    def encode(self) -> str:
        raw = f"{self.created_at.isoformat()}|{self.id}".encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def decode(cls, value: str) -> "Cursor":
        try:
            padded = value + "=" * (-len(value) % 4)
            created_at, id_ = base64.urlsafe_b64decode(padded).decode().split("|")
            return cls(datetime.fromisoformat(created_at), uuid.UUID(id_))
        except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
            raise AppError(422, "invalid_cursor", "That page cursor isn't valid.") from exc

    @classmethod
    def of(cls, message: Message) -> "Cursor":
        return cls(message.created_at, message.id)


def message_out(message: Message, author_name: str | None) -> MessageOut:
    return MessageOut(
        id=message.id,
        conversation_id=message.conversation_id,
        author_id=message.author_id,
        author_name=author_name,
        body=message.body,
        client_msg_id=message.client_msg_id,
        created_at=message.created_at,
        removed=message.removed_at is not None,
    )


class ChatService:
    """Conversations and messages. Only active members may read or send; a block makes both
    members inactive, so it applies here without extra checks."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def conversation(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID, presence: Presence
    ) -> ConversationOut:
        found = await self.conversations(user_id, presence, only=conversation_id)
        if not found:
            raise AppError(404, "not_found", NOT_FOUND)
        return found[0]

    async def conversations(
        self, user_id: uuid.UUID, presence: Presence, *, only: uuid.UUID | None = None
    ) -> list[ConversationOut]:
        """Every active conversation of both kinds. A project conversation is one row here
        whatever its team size; its direct partner is looked up separately, keyed by
        conversation id, so a team of N members never produces N-1 rows."""
        me = aliased(ConversationMember)
        # The (conversation_id, created_at, id) index serves this correlated subquery, so the
        # ORDER BY (and the LIMIT after it) costs no extra scan.
        last_activity = (
            select(func.max(Message.created_at))
            .where(Message.conversation_id == Conversation.id)
            .correlate(Conversation)
            .scalar_subquery()
        )
        query = (
            select(Conversation, Project.title)
            .join(me, and_(me.conversation_id == Conversation.id, me.user_id == user_id))
            .outerjoin(Project, Project.id == Conversation.project_id)
            .where(me.active)
            .order_by(func.coalesce(last_activity, Conversation.created_at).desc())
            .limit(MAX_CONVERSATIONS)
        )
        if only is not None:
            query = query.where(Conversation.id == only)
        rows = (await self.db.execute(query)).all()
        ids = [conversation.id for conversation, _ in rows]
        direct_ids = [conversation.id for conversation, _ in rows if conversation.kind == "direct"]

        others: dict[uuid.UUID, tuple[uuid.UUID, str | None]] = {}
        if direct_ids:
            other = aliased(ConversationMember)
            other_rows = (
                await self.db.execute(
                    select(other.conversation_id, other.user_id, Profile.display_name)
                    .outerjoin(Profile, Profile.user_id == other.user_id)
                    .where(other.conversation_id.in_(direct_ids), other.user_id != user_id)
                )
            ).all()
            others = {
                conversation_id: (other_id, name) for conversation_id, other_id, name in other_rows
            }

        latest = {
            message.conversation_id: (message, name)
            for message, name in (
                await self.db.execute(
                    select(Message, Profile.display_name)
                    .outerjoin(Profile, Profile.user_id == Message.author_id)
                    .where(Message.conversation_id.in_(ids))
                    .ext(distinct_on(Message.conversation_id))
                    .order_by(Message.conversation_id, Message.created_at.desc(), Message.id.desc())
                )
            ).all()
        }
        items = []
        for conversation, project_title in rows:
            # A direct conversation always has an "other" member; once one side has deleted
            # their account, the row lingers only until the last member goes too (see
            # AuthService.delete_account), so it's effectively gone already.
            if conversation.kind == "direct" and conversation.id not in others:
                continue
            items.append(
                ConversationOut(
                    id=conversation.id,
                    kind=cast(Literal["direct", "project"], conversation.kind),
                    name=project_title if conversation.kind == "project" else None,
                    project_id=conversation.project_id,
                    other=OtherMember(
                        user_id=others[conversation.id][0],
                        display_name=others[conversation.id][1],
                        online=presence.is_online(others[conversation.id][0]),
                    )
                    if conversation.kind == "direct"
                    else None,
                    last_message=message_out(*latest[conversation.id])
                    if conversation.id in latest
                    else None,
                    updated_at=latest[conversation.id][0].created_at
                    if conversation.id in latest
                    else conversation.created_at,
                )
            )
        return sorted(items, key=lambda item: item.updated_at, reverse=True)

    async def page(
        self,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        *,
        before: str | None,
        after: str | None,
        limit: int,
    ) -> MessagePage:
        if before is not None and after is not None:
            raise AppError(422, "invalid_cursor", "Use either before or after, not both.")
        await self.require_member(user_id, conversation_id)
        key = tuple_(Message.created_at, Message.id)
        query = (
            select(Message, Profile.display_name)
            .outerjoin(Profile, Profile.user_id == Message.author_id)
            .where(Message.conversation_id == conversation_id)
        )

        if after is not None:
            cursor = Cursor.decode(after)
            newer = (
                await self.db.execute(
                    query.where(key > tuple_(cursor.created_at, cursor.id))
                    .order_by(Message.created_at, Message.id)
                    .limit(limit)
                )
            ).all()
            newest = Cursor.of(newer[-1][0]).encode() if newer else after
            return MessagePage(
                messages=[message_out(m, name) for m, name in newer], before=None, after=newest
            )

        if before is not None:
            cursor = Cursor.decode(before)
            query = query.where(key < tuple_(cursor.created_at, cursor.id))
        older_first = (
            await self.db.execute(
                query.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit + 1)
            )
        ).all()
        has_more = len(older_first) > limit
        page = list(reversed(older_first[:limit]))
        return MessagePage(
            messages=[message_out(m, name) for m, name in page],
            before=Cursor.of(page[0][0]).encode() if has_more and page else None,
            after=Cursor.of(page[-1][0]).encode() if page else after,
        )

    async def send(
        self, author_id: uuid.UUID, conversation_id: uuid.UUID, client_msg_id: uuid.UUID, body: str
    ) -> tuple[MessageOut, list[uuid.UUID]]:
        """Store a message once per (author, client_msg_id) and return it with the active members
        to deliver it to. The conversation row is locked first, so messages commit in the order
        of their created_at and catch-up by cursor never skips one."""
        locked = await self.db.scalar(
            select(Conversation.id).where(Conversation.id == conversation_id).with_for_update()
        )
        members = list(
            await self.db.scalars(
                select(ConversationMember.user_id).where(
                    ConversationMember.conversation_id == conversation_id,
                    ConversationMember.active,
                )
            )
        )
        if locked is None or author_id not in members:
            await self.db.rollback()
            raise AppError(404, "not_found", NOT_FOUND)

        await self.db.execute(
            pg_insert(Message)
            .values(
                conversation_id=conversation_id,
                author_id=author_id,
                client_msg_id=client_msg_id,
                body=body,
            )
            .on_conflict_do_nothing(index_elements=[Message.author_id, Message.client_msg_id])
        )
        message = await self.db.scalar(
            select(Message).where(
                Message.author_id == author_id, Message.client_msg_id == client_msg_id
            )
        )
        author_name = await self.db.scalar(
            select(Profile.display_name).where(Profile.user_id == author_id)
        )
        await self.db.commit()
        if message is None or message.conversation_id != conversation_id:
            # The same client id was already used for another conversation.
            raise AppError(409, "client_msg_id_reused", "Send this message again.")
        return message_out(message, author_name), members

    async def require_member(self, user_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
        active = await self.db.scalar(
            select(ConversationMember.active).where(
                ConversationMember.conversation_id == conversation_id,
                ConversationMember.user_id == user_id,
            )
        )
        if not active:
            raise AppError(404, "not_found", NOT_FOUND)

    async def partners(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        """Students who share an active conversation with this one (presence goes to them). A
        block between two ordinary teammates leaves them both active in the team chat, so it's
        excluded here rather than relying on conversation membership alone."""
        me, other = aliased(ConversationMember), aliased(ConversationMember)
        return list(
            await self.db.scalars(
                select(other.user_id)
                .join(me, me.conversation_id == other.conversation_id)
                .where(
                    me.user_id == user_id,
                    me.active,
                    other.active,
                    other.user_id != user_id,
                    ~blocked_between(user_id, other.user_id),
                )
                .distinct()
            )
        )
