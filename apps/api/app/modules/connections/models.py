import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

CONNECTION_STATES = ("pending", "accepted", "declined", "withdrawn", "ended")
# The states that count as "already connected or asked", one per pair of students.
OPEN_STATES_SQL = "state IN ('pending', 'accepted')"


class Connection(Base):
    """A request from sender to recipient. Only the recipient can accept or decline it, only the
    sender can withdraw it, and a block ends it."""

    __tablename__ = "connections"
    __table_args__ = (
        CheckConstraint(
            f"state IN ({', '.join(repr(s) for s in CONNECTION_STATES)})", name="state_valid"
        ),
        CheckConstraint("sender_id <> recipient_id", name="not_self"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sender_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    state: Mapped[str] = mapped_column(String(16), server_default="pending")
    # Set on acceptance, in the same transaction that creates the direct conversation.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# One open connection per unordered pair: A→B and B→A can't both be pending, and retries or
# crossed requests can't create a second row.
OPEN_PAIR_INDEX = Index(
    "uq_connections_open_pair",
    func.least(Connection.sender_id, Connection.recipient_id),
    func.greatest(Connection.sender_id, Connection.recipient_id),
    unique=True,
    postgresql_where=text(OPEN_STATES_SQL),
)
