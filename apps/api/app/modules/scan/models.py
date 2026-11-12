import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# The predicate of the partial unique index below: at most one active code per student.
ACTIVE_SQL = "active"


class ProfileShareToken(Base):
    """A student's Scan to Connect code. The token itself is never stored: it is
    HMAC-SHA256(share key, row id), so the server can show the current link again, and only its
    SHA-256 is kept for lookups. Rotating revokes the row and issues a new one; at most one is
    active per student."""

    __tablename__ = "profile_share_tokens"
    __table_args__ = (
        CheckConstraint("active = (revoked_at IS NULL)", name="active_matches_revoked"),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
        Index(
            "uq_profile_share_tokens_one_active",
            "user_id",
            unique=True,
            postgresql_where=text(ACTIVE_SQL),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Hex SHA-256 of the token.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
