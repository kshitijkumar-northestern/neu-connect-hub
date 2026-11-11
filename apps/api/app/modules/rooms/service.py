import math
import uuid
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limit import Limit, RateLimiter
from app.modules.chat.models import Conversation, ConversationMember
from app.modules.profiles.models import Profile
from app.modules.rooms.access import is_conversation_member, room_for_member
from app.modules.rooms.models import ACTIVE_SQL, Room
from app.modules.rooms.schemas import IceServersOut, ParticipantOut, RoomOut
from app.modules.rooms.turn import generate_turn_credential
from app.modules.teams.models import Project
from app.realtime.calls import CallRegistry

START_LIMIT = Limit(max_events=20, window_seconds=3600)
ICE_SERVERS_LIMIT = Limit(max_events=60, window_seconds=3600)

NOT_FOUND = "This call isn't available."
ROOM_ENDED = "This call has ended."


class RoomService:
    """Calls: one active room per conversation, the registry of who's in it, and the TURN
    credentials for connecting. Membership is always checked in Postgres, never cached."""

    def __init__(
        self, db: AsyncSession, limiter: RateLimiter, calls: CallRegistry, settings: Settings
    ) -> None:
        self.db = db
        self.limiter = limiter
        self.calls = calls
        self.settings = settings

    async def start(self, user_id: uuid.UUID, conversation_id: uuid.UUID) -> tuple[RoomOut, bool]:
        """Returns the room and whether it was just created (201) or already going (200)."""
        self._limit_start(user_id)
        if not await is_conversation_member(self.db, user_id, conversation_id):
            raise AppError(404, "not_found", NOT_FOUND)

        candidate_id = uuid.uuid4()
        await self.db.execute(
            pg_insert(Room)
            .values(id=candidate_id, conversation_id=conversation_id, created_by=user_id)
            .on_conflict_do_nothing(
                index_elements=[Room.conversation_id], index_where=text(ACTIVE_SQL)
            )
        )
        # Read back inside this same transaction, before committing: whichever insert won the
        # partial unique index is the active room, ours or a concurrent starter's.
        room = await self.db.scalar(
            select(Room).where(Room.conversation_id == conversation_id, Room.room_state == "active")
        )
        if room is None:  # pragma: no cover - the insert above always creates or conflicts
            raise AssertionError("no active room after an unconflicted insert")
        created = room.id == candidate_id
        out = await self._room_out(user_id, room)
        await self.db.commit()
        return out, created

    async def get(self, user_id: uuid.UUID, room_id: uuid.UUID) -> RoomOut:
        room = await room_for_member(self.db, user_id, room_id)
        if room is None:
            raise AppError(404, "not_found", NOT_FOUND)
        return await self._room_out(user_id, room)

    async def ice_servers(self, user_id: uuid.UUID, room_id: uuid.UUID) -> IceServersOut:
        self._limit_ice_servers(user_id)
        room = await room_for_member(self.db, user_id, room_id)
        if room is None:
            raise AppError(404, "not_found", NOT_FOUND)
        if room.room_state == "ended":
            raise AppError(409, "room_ended", ROOM_ENDED)

        servers: list[dict[str, Any]] = []
        if self.settings.stun_url_list:
            servers.append({"urls": self.settings.stun_url_list})

        expires_at = None
        if self.settings.turn_url_list and self.settings.turn_secret is not None:
            credential = generate_turn_credential(
                self.settings.turn_secret.get_secret_value(),
                user_id,
                self.settings.turn_credential_ttl_seconds,
                datetime.now(UTC),
            )
            servers.append(
                {
                    "urls": self.settings.turn_url_list,
                    "username": credential.username,
                    "credential": credential.credential,
                }
            )
            expires_at = credential.expires_at
        return IceServersOut(ice_servers=servers, expires_at=expires_at)

    # --- assembling a Room -------------------------------------------------------------------

    async def _room_out(self, user_id: uuid.UUID, room: Room) -> RoomOut:
        conversation = await self.db.get(Conversation, room.conversation_id)
        if conversation is None:  # pragma: no cover - CASCADE keeps these in step
            raise AppError(404, "not_found", NOT_FOUND)
        name = await self._name(user_id, conversation)
        participants = await self._participants(room.id)
        return RoomOut(
            id=room.id,
            conversation_id=room.conversation_id,
            kind=cast(Literal["direct", "project"], conversation.kind),
            name=name,
            project_id=conversation.project_id,
            room_state=cast(Literal["active", "ended"], room.room_state),
            capacity=self.settings.room_capacity,
            participants=participants,
            created_at=room.created_at,
            ended_at=room.ended_at,
        )

    async def _name(self, user_id: uuid.UUID, conversation: Conversation) -> str | None:
        if conversation.kind == "project":
            return await self.db.scalar(
                select(Project.title).where(Project.id == conversation.project_id)
            )
        other = aliased(ConversationMember)
        return await self.db.scalar(
            select(Profile.display_name)
            .select_from(other)
            .outerjoin(Profile, Profile.user_id == other.user_id)
            .where(other.conversation_id == conversation.id, other.user_id != user_id)
        )

    async def _participants(self, room_id: uuid.UUID) -> list[ParticipantOut]:
        """Who's in the call now, earliest joiner first (the registry's order), with names
        looked up afterwards so that order survives the join."""
        joined = self.calls.participants(room_id)
        if not joined:
            return []
        names = dict(
            (
                await self.db.execute(
                    select(Profile.user_id, Profile.display_name).where(Profile.user_id.in_(joined))
                )
            ).all()
        )
        return [ParticipantOut(user_id=uid, display_name=names.get(uid)) for uid in joined]

    # --- rate limits --------------------------------------------------------------------------

    def _limit_start(self, user_id: uuid.UUID) -> None:
        retry_after = self.limiter.hit("room-start", str(user_id), START_LIMIT)
        if retry_after is not None:
            raise AppError(
                429,
                "rate_limited",
                "Too many calls started. Please wait and try again.",
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            )

    def _limit_ice_servers(self, user_id: uuid.UUID) -> None:
        retry_after = self.limiter.hit("room-ice-servers", str(user_id), ICE_SERVERS_LIMIT)
        if retry_after is not None:
            raise AppError(
                429,
                "rate_limited",
                "Too many requests. Please wait and try again.",
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            )
