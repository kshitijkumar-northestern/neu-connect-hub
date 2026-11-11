import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

ROOM_STATES = ("active", "ended")

# The predicates of the partial unique indexes below. ON CONFLICT must repeat them word for word,
# because Postgres only infers a partial index when the WHERE text matches.
ACTIVE_SQL = "room_state = 'active'"
PRESENT_SQL = "left_at IS NULL"


class Room(Base):
    """A call in a conversation (a direct one, or a project's team chat), so whoever may read the
    conversation may join its call. One active room per conversation; it ends when the last
    participant leaves, and the next call starts a new room."""

    __tablename__ = "rooms"
    __table_args__ = (
        CheckConstraint(
            f"room_state IN ({', '.join(repr(s) for s in ROOM_STATES)})", name="room_state_valid"
        ),
        CheckConstraint(
            "(room_state = 'ended') = (ended_at IS NOT NULL)", name="ended_matches_state"
        ),
        Index(
            "uq_rooms_one_active_per_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text(ACTIVE_SQL),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    # Who started the call. Cleared if they delete their account: the call belongs to the
    # conversation, so it carries on for everyone else.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    room_state: Mapped[str] = mapped_column(String(8), server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RoomMember(Base):
    """The attendance log: one row per join, closed by its leave. Never any media."""

    __tablename__ = "room_members"
    __table_args__ = (
        CheckConstraint("left_at IS NULL OR left_at >= joined_at", name="left_after_joined"),
        # A student is in a room at most once at a time (one socket per student per room).
        Index(
            "uq_room_members_present",
            "room_id",
            "user_id",
            unique=True,
            postgresql_where=text(PRESENT_SQL),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
