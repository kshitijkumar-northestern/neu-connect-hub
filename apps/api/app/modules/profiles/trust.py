"""Blocking and reporting: the account-safety actions every student can take on another."""

import math
import uuid

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import Insert as PgInsert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.rate_limit import Limit, RateLimiter
from app.modules.chat.models import ConversationMember
from app.modules.connections.models import Connection
from app.modules.profiles.models import Block, Profile, Report
from app.modules.profiles.schemas import BlockedUser

# A generous allowance: reporting is rare, but a moderation incident can need several reports
# in a short window, and the pair-level unique index already stops queue flooding by itself.
REPORTS_PER_USER = Limit(max_events=10, window_seconds=3600)

NOT_FOUND = "Account not found."
# The settings page lists blocks; beyond this many, only the newest are shown.
MAX_LISTED_BLOCKS = 500


class TrustService:
    """Blocks and reports. Both insert with ON CONFLICT DO NOTHING so a repeat is a no-op
    rather than a read-then-write race: nothing here reads state before deciding to write."""

    def __init__(self, db: AsyncSession, limiter: RateLimiter) -> None:
        self.db = db
        self.limiter = limiter

    async def block(self, blocker_id: uuid.UUID, target_id: uuid.UUID) -> None:
        """Block, and in the same transaction end any open connection between the two and
        deactivate their direct conversation."""
        self._not_self(blocker_id, target_id)
        statement = (
            pg_insert(Block)
            .values(blocker_id=blocker_id, blocked_id=target_id)
            .on_conflict_do_nothing(index_elements=[Block.blocker_id, Block.blocked_id])
        )
        await self._insert_or_not_found(statement)
        pair = or_(
            and_(Connection.sender_id == blocker_id, Connection.recipient_id == target_id),
            and_(Connection.sender_id == target_id, Connection.recipient_id == blocker_id),
        )
        ended = await self.db.scalars(
            update(Connection)
            .where(pair, Connection.state.in_(("pending", "accepted")))
            .values(state="ended", responded_at=func.now())
            .returning(Connection.conversation_id)
        )
        conversations = [conversation for conversation in ended if conversation is not None]
        if conversations:
            await self.db.execute(
                update(ConversationMember)
                .where(ConversationMember.conversation_id.in_(conversations))
                .values(active=False)
            )
        await self.db.commit()

    async def unblock(self, blocker_id: uuid.UUID, target_id: uuid.UUID) -> None:
        await self.db.execute(
            delete(Block).where(Block.blocker_id == blocker_id, Block.blocked_id == target_id)
        )
        await self.db.commit()

    async def list_blocks(self, blocker_id: uuid.UUID) -> list[BlockedUser]:
        rows = await self.db.execute(
            select(Block.blocked_id, Profile.display_name, Block.created_at)
            .select_from(Block)
            .outerjoin(Profile, Profile.user_id == Block.blocked_id)
            .where(Block.blocker_id == blocker_id)
            .order_by(Block.created_at.desc())
            .limit(MAX_LISTED_BLOCKS)
        )
        return [
            BlockedUser(user_id=blocked_id, display_name=display_name, blocked_at=blocked_at)
            for blocked_id, display_name, blocked_at in rows
        ]

    async def report(
        self, reporter_id: uuid.UUID, target_id: uuid.UUID, reason: str, details: str
    ) -> None:
        # Counted before anything else, so repeated attempts (including self-reports and
        # reports of unknown accounts) all count against the limit.
        self._limit(reporter_id)
        self._not_self(reporter_id, target_id)
        statement = (
            pg_insert(Report)
            .values(
                reporter_id=reporter_id, target_user_id=target_id, reason=reason, details=details
            )
            .on_conflict_do_nothing(
                index_elements=[Report.reporter_id, Report.target_user_id],
                # A literal, matching the partial index's predicate exactly: Postgres only
                # infers a partial unique index when the WHERE clause text matches.
                index_where=text("status = 'open'"),
            )
        )
        await self._insert_or_not_found(statement)
        await self.db.commit()

    # --- helpers ----------------------------------------------------------------------------

    async def _insert_or_not_found(self, statement: PgInsert) -> None:
        try:
            await self.db.execute(statement)
        except IntegrityError:
            await self.db.rollback()
            raise AppError(404, "not_found", NOT_FOUND) from None

    def _not_self(self, actor_id: uuid.UUID, target_id: uuid.UUID) -> None:
        if actor_id == target_id:
            raise AppError(422, "cannot_target_self", "You can't target your own account.")

    def _limit(self, reporter_id: uuid.UUID) -> None:
        retry_after = self.limiter.hit("report", str(reporter_id), REPORTS_PER_USER)
        if retry_after is not None:
            raise AppError(
                429,
                "rate_limited",
                "Too many reports. Please wait and try again.",
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            )
