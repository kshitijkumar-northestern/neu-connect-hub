import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

GOALS = ("studying", "project_development", "research", "mentorship")
SKILL_KINDS = ("has", "seeking")
# Stored as proficiency 1-3 so ranking can compare levels; the API uses the names.
LEVELS = ("beginner", "intermediate", "advanced")
REPORT_REASONS = ("spam", "harassment", "inappropriate_content", "impersonation", "other")
REPORT_STATUSES = ("open", "resolved", "dismissed")

DISPLAY_NAME_MAX = 80
HEADLINE_MAX = 120
BIO_MAX = 1000
REPORT_DETAILS_MAX = 1000
MINUTES_PER_DAY = 1440


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


# --- the controlled taxonomy (rows ship with the migration) ---------------------------------


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(SmallInteger, Identity(), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(40))


class Interest(Base):
    __tablename__ = "interests"

    id: Mapped[int] = mapped_column(SmallInteger, Identity(), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(80))


# --- profiles -------------------------------------------------------------------------------


class Profile(Base):
    """A student's opt-in profile. Hidden from everyone else until `discoverable` is set."""

    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint("char_length(display_name) > 0", name="display_name_present"),
        CheckConstraint(
            f"goals <@ ARRAY[{', '.join(repr(g) for g in GOALS)}]::varchar[]", name="goals_valid"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    display_name: Mapped[str] = mapped_column(String(DISPLAY_NAME_MAX))
    headline: Mapped[str] = mapped_column(String(HEADLINE_MAX), server_default="")
    bio: Mapped[str] = mapped_column(String(BIO_MAX), server_default="")
    goals: Mapped[list[str]] = mapped_column(ARRAY(String(32)), server_default="{}")
    # IANA name; availability slots are wall-clock times in this zone.
    timezone: Mapped[str] = mapped_column(String(64))
    discoverable: Mapped[bool] = mapped_column(Boolean, server_default="false")
    # Consent to logging match feedback for model evaluation.
    feedback_consent: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProfileSkill(Base):
    """A skill the student has (with a level) or is looking for in collaborators."""

    __tablename__ = "profile_skills"
    __table_args__ = (
        CheckConstraint(_in("kind", SKILL_KINDS), name="kind_valid"),
        CheckConstraint(
            "(kind = 'has' AND proficiency BETWEEN 1 AND 3)"
            " OR (kind = 'seeking' AND proficiency IS NULL)",
            name="proficiency_matches_kind",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.user_id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"), primary_key=True, index=True)
    kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    proficiency: Mapped[int | None] = mapped_column(SmallInteger)


class ProfileInterest(Base):
    __tablename__ = "profile_interests"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.user_id", ondelete="CASCADE"), primary_key=True
    )
    interest_id: Mapped[int] = mapped_column(
        ForeignKey("interests.id"), primary_key=True, index=True
    )


class AvailabilitySlot(Base):
    """A weekly time range: weekday 0 is Monday, minutes count from local midnight."""

    __tablename__ = "availability_slots"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_valid"),
        CheckConstraint(
            f"0 <= start_min AND start_min < end_min AND end_min <= {MINUTES_PER_DAY}",
            name="range_valid",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.user_id", ondelete="CASCADE"), primary_key=True
    )
    weekday: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    start_min: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    end_min: Mapped[int] = mapped_column(SmallInteger)


# --- trust: blocks and reports --------------------------------------------------------------


class Block(Base):
    """A block hides each student from the other everywhere, whoever created it."""

    __tablename__ = "blocks"
    __table_args__ = (CheckConstraint("blocker_id <> blocked_id", name="not_self"),)

    blocker_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    blocked_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Report(Base):
    """A report for moderators. It outlives the reporter's account (reporter becomes NULL) but
    not the reported account."""

    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint(_in("reason", REPORT_REASONS), name="reason_valid"),
        CheckConstraint(_in("status", REPORT_STATUSES), name="status_valid"),
        CheckConstraint("reporter_id <> target_user_id", name="not_self"),
        # One open report per reporter and target, so repeats can't flood the review queue.
        Index(
            "uq_reports_open_pair",
            "reporter_id",
            "target_user_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    reporter_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    target_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    reason: Mapped[str] = mapped_column(String(32))
    details: Mapped[str] = mapped_column(String(REPORT_DETAILS_MAX), server_default="")
    status: Mapped[str] = mapped_column(String(16), server_default="open", index=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
