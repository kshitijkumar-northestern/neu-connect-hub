import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# all-MiniLM-L6-v2 produces 384-dimension vectors; the column refuses any other size.
EMBEDDING_DIMENSIONS = 384


class Embedding(Base):
    """A discoverable profile's vector for matching. Written only by the background worker; a
    profile save marks it stale in the same transaction, and opting out of discovery or deleting
    the account removes it."""

    __tablename__ = "embeddings"
    __table_args__ = (
        CheckConstraint(
            "stale OR (embedding IS NOT NULL AND model_version IS NOT NULL"
            " AND source_hash IS NOT NULL)",
            name="fresh_has_vector",
        ),
        # The worker claims the oldest stale rows first.
        Index("ix_embeddings_stale", "updated_at", postgresql_where=text("stale")),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.user_id", ondelete="CASCADE"), primary_key=True
    )
    model_version: Mapped[str | None] = mapped_column(String(128))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    # SHA-256 of the model version and the encoded text, so unchanged profiles aren't re-encoded.
    source_hash: Mapped[str | None] = mapped_column(String(64))
    stale: Mapped[bool] = mapped_column(Boolean, server_default="true")
    # Set while a worker encodes the row. Claims skip rows under someone else's lease; a batch
    # that keeps failing sits out until its lease expires instead of blocking the queue; a profile
    # save clears it, so a write-back for an older claim is refused.
    claimed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
