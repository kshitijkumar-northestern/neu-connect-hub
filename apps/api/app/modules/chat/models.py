import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

CONVERSATION_KINDS = ("direct", "project")
MESSAGE_MAX = 4000


class Conversation(Base):
    """A direct conversation is created when a connection is accepted; a project's team chat is
    created with the project and goes with it. Messages belong to a conversation."""

    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("kind IN ('direct', 'project')", name="kind_valid"),
        CheckConstraint(
            "(kind = 'project') = (project_id IS NOT NULL)", name="project_matches_kind"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16))
    # One team chat per project.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConversationMember(Base):
    """Membership is never deleted on a block: it turns inactive, so history stays consistent."""

    __tablename__ = "conversation_members"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    """A message. When its author deletes their account the row stays as a tombstone (no body,
    no author), so the other members see "message removed" instead of a gap."""

    __tablename__ = "messages"
    __table_args__ = (
        # A resent client_msg_id from the same author is the same message (idempotent sends).
        UniqueConstraint("author_id", "client_msg_id"),
        CheckConstraint("(removed_at IS NULL) = (body IS NOT NULL)", name="body_matches_removed"),
        # Keyset paging and catch-up after a reconnect read in (created_at, id) order.
        Index("ix_messages_conversation_created_id", "conversation_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    client_msg_id: Mapped[uuid.UUID]
    body: Mapped[str | None] = mapped_column(String(MESSAGE_MAX))
    # clock_timestamp, not now(): sends lock the conversation first, so this records commit order.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
