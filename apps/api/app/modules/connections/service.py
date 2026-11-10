import math
import uuid
from typing import Literal, cast

from sqlalchemy import ColumnElement, Select, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.rate_limit import Limit, RateLimiter
from app.modules.auth.models import User
from app.modules.chat.models import Conversation, ConversationMember
from app.modules.connections.models import OPEN_STATES_SQL, Connection
from app.modules.connections.schemas import ConnectionOut
from app.modules.profiles.models import Profile
from app.modules.profiles.visibility import blocked_between, visible_to

REQUESTS_PER_USER = Limit(max_events=20, window_seconds=3600)
MAX_LISTED = 500

NOT_FOUND = "Account not found."
REQUEST_NOT_FOUND = "Connection request not found."
CLOSED = "This request is no longer open."


class ConnectionService:
    """Requests between students. Every change is one conditional UPDATE guarded on who may make
    it and the current state, so retries and concurrent requests can't produce a second outcome."""

    def __init__(self, db: AsyncSession, limiter: RateLimiter) -> None:
        self.db = db
        self.limiter = limiter

    async def request(self, sender_id: uuid.UUID, target_id: uuid.UUID) -> ConnectionOut:
        self._limit(sender_id)
        if sender_id == target_id:
            raise AppError(422, "cannot_target_self", "You can't connect with yourself.")
        # Only students the sender may see can be asked: hidden, blocked and missing look alike.
        eligible = await self.db.scalar(
            select(Profile.user_id)
            .join(User, User.id == Profile.user_id)
            .where(Profile.user_id == target_id, visible_to(sender_id))
        )
        if eligible is None:
            raise AppError(404, "not_found", NOT_FOUND)

        # The partial unique index on the unordered pair absorbs repeats and crossed requests.
        connection_id = await self.db.scalar(
            pg_insert(Connection)
            .values(sender_id=sender_id, recipient_id=target_id)
            .on_conflict_do_nothing(
                index_elements=[
                    func.least(Connection.sender_id, Connection.recipient_id),
                    func.greatest(Connection.sender_id, Connection.recipient_id),
                ],
                index_where=text(OPEN_STATES_SQL),
            )
            .returning(Connection.id)
        )
        if connection_id is None:
            await self.db.rollback()
            raise AppError(
                409, "connection_exists", "You already have a request or connection with them."
            )
        await self.db.commit()
        return await self._view(sender_id, connection_id)

    async def accept(self, recipient_id: uuid.UUID, connection_id: uuid.UUID) -> ConnectionOut:
        """Accept and create the direct conversation in one transaction. The row lock taken by
        the UPDATE makes concurrent accepts wait; they then see it accepted and agree."""
        sender_id = await self.db.scalar(
            update(Connection)
            .where(
                Connection.id == connection_id,
                Connection.recipient_id == recipient_id,
                Connection.state == "pending",
                ~blocked_between(Connection.sender_id, Connection.recipient_id),
            )
            .values(state="accepted", responded_at=func.now())
            .returning(Connection.sender_id)
        )
        if sender_id is None:
            await self.db.rollback()
            state = await self._state(connection_id, Connection.recipient_id == recipient_id)
            if state == "accepted":
                return await self._view(recipient_id, connection_id)
            if state is None or state == "pending":  # not theirs, unknown, or blocked meanwhile
                raise AppError(404, "not_found", REQUEST_NOT_FOUND)
            raise AppError(409, "connection_closed", CLOSED)

        conversation = Conversation(kind="direct")
        self.db.add(conversation)
        await self.db.flush()
        self.db.add_all(
            [
                ConversationMember(conversation_id=conversation.id, user_id=member)
                for member in (sender_id, recipient_id)
            ]
        )
        await self.db.execute(
            update(Connection)
            .where(Connection.id == connection_id)
            .values(conversation_id=conversation.id)
        )
        await self.db.commit()
        return await self._view(recipient_id, connection_id)

    async def decline(self, recipient_id: uuid.UUID, connection_id: uuid.UUID) -> None:
        await self._close(
            connection_id, Connection.recipient_id == recipient_id, to_state="declined"
        )

    async def withdraw(self, sender_id: uuid.UUID, connection_id: uuid.UUID) -> None:
        await self._close(connection_id, Connection.sender_id == sender_id, to_state="withdrawn")

    async def list(self, user_id: uuid.UUID) -> list[ConnectionOut]:
        rows = await self.db.execute(
            self._select(user_id)
            .where(
                or_(Connection.sender_id == user_id, Connection.recipient_id == user_id),
                Connection.state.in_(("pending", "accepted")),
            )
            .order_by(Connection.created_at.desc())
            .limit(MAX_LISTED)
        )
        return [self._out(user_id, connection, name) for connection, name in rows]

    # --- helpers ----------------------------------------------------------------------------

    async def _close(
        self, connection_id: uuid.UUID, allowed: ColumnElement[bool], to_state: str
    ) -> None:
        """Move a pending request to declined or withdrawn; repeating it is a no-op."""
        closed = await self.db.scalar(
            update(Connection)
            .where(Connection.id == connection_id, allowed, Connection.state == "pending")
            .values(state=to_state, responded_at=func.now())
            .returning(Connection.id)
        )
        if closed is not None:
            await self.db.commit()
            return
        await self.db.rollback()
        state = await self._state(connection_id, allowed)
        if state is None:
            raise AppError(404, "not_found", REQUEST_NOT_FOUND)
        if state != to_state:
            raise AppError(409, "connection_closed", CLOSED)

    async def _state(self, connection_id: uuid.UUID, allowed: ColumnElement[bool]) -> str | None:
        return await self.db.scalar(
            select(Connection.state).where(Connection.id == connection_id, allowed)
        )

    def _select(self, viewer_id: uuid.UUID) -> Select[Connection, str]:
        other = func.coalesce(func.nullif(Connection.sender_id, viewer_id), Connection.recipient_id)
        return select(Connection, Profile.display_name).outerjoin(Profile, Profile.user_id == other)

    async def _view(self, viewer_id: uuid.UUID, connection_id: uuid.UUID) -> ConnectionOut:
        connection, name = (
            await self.db.execute(self._select(viewer_id).where(Connection.id == connection_id))
        ).one()
        return self._out(viewer_id, connection, name)

    def _out(self, viewer_id: uuid.UUID, connection: Connection, name: str | None) -> ConnectionOut:
        outgoing = connection.sender_id == viewer_id
        return ConnectionOut(
            id=connection.id,
            state=cast(Literal["pending", "accepted"], connection.state),
            direction="outgoing" if outgoing else "incoming",
            user_id=connection.recipient_id if outgoing else connection.sender_id,
            display_name=name,
            created_at=connection.created_at,
            responded_at=connection.responded_at,
            conversation_id=connection.conversation_id,
        )

    def _limit(self, sender_id: uuid.UUID) -> None:
        retry_after = self.limiter.hit("connection-request", str(sender_id), REQUESTS_PER_USER)
        if retry_after is not None:
            raise AppError(
                429,
                "rate_limited",
                "Too many connection requests. Please wait and try again.",
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            )
