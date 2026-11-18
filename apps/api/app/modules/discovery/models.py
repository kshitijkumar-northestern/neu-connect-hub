import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

FEEDBACK_ACTIONS = ("connected", "dismissed")


class MatchFeedback(Base):
    """What a student did with a recommendation, kept only for evaluating the matcher. Written
    only while the viewer consents to feedback; withdrawing consent, leaving discovery or
    deleting either account removes it. Holds no text, scores or reasons."""

    __tablename__ = "match_feedback"
    __table_args__ = (
        CheckConstraint(
            f"action IN ({', '.join(repr(a) for a in FEEDBACK_ACTIONS)})", name="action_valid"
        ),
        CheckConstraint("viewer_id <> candidate_id", name="not_self"),
        # One row per viewer, candidate and action: repeats and double clicks add nothing.
        UniqueConstraint("viewer_id", "candidate_id", "action", name="uq_match_feedback_action"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    viewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
