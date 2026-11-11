import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PROJECT_STATUSES = ("open", "full", "closed")
MEMBER_ROLES = ("owner", "member")
APPLICATION_STATUSES = ("pending", "accepted", "rejected", "withdrawn")

TITLE_MAX = 140
SUMMARY_MAX = 2000
ROLE_TITLE_MAX = 80
CAPACITY_MAX = 50
OPEN_SLOTS_MAX = 50
MAX_ROLES = 10
MAX_ROLE_SKILLS = 10

# The predicate of the partial unique index below. ON CONFLICT must repeat it word for word,
# because Postgres only infers a partial index when the WHERE text matches.
PENDING_SQL = "status = 'pending'"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Project(Base):
    """A team a student leads. Capacity is the team size including the owner, so a capacity
    of 1 is full from the start. Only `open` projects take applications."""

    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(_in("status", PROJECT_STATUSES), name="status_valid"),
        CheckConstraint(f"capacity BETWEEN 1 AND {CAPACITY_MAX}", name="capacity_valid"),
        CheckConstraint("char_length(title) > 0", name="title_present"),
        # The public list reads open projects, newest first.
        Index("ix_projects_status_created_at", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(TITLE_MAX))
    summary: Mapped[str] = mapped_column(String(SUMMARY_MAX), server_default="")
    capacity: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(String(8), server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProjectRole(Base):
    """A place on the team the owner wants filled: a title, the skills it needs and how many
    students it takes."""

    __tablename__ = "project_roles"
    __table_args__ = (
        CheckConstraint(f"open_slots BETWEEN 1 AND {OPEN_SLOTS_MAX}", name="open_slots_valid"),
        CheckConstraint("char_length(title) > 0", name="title_present"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(ROLE_TITLE_MAX))
    open_slots: Mapped[int] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectSkill(Base):
    """A skill a role requires."""

    __tablename__ = "project_skills"

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("project_roles.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"), primary_key=True, index=True)


class ProjectMember(Base):
    """A student on the team. The owner is a member too (with no role), so counting members
    against capacity needs no special case."""

    __tablename__ = "project_members"
    __table_args__ = (
        CheckConstraint(_in("member_role", MEMBER_ROLES), name="member_role_valid"),
        CheckConstraint("member_role = 'member' OR role_id IS NULL", name="owner_has_no_role"),
        # Exactly one owner per project.
        Index(
            "uq_project_members_one_owner",
            "project_id",
            unique=True,
            postgresql_where=text("member_role = 'owner'"),
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project_roles.id", ondelete="SET NULL"), index=True
    )
    member_role: Mapped[str] = mapped_column(String(8), server_default="member")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Application(Base):
    """A request to join a project, optionally for one role. Only the owner decides it and
    only the applicant can withdraw it. Decided applications are kept, so a student can apply
    again after a rejection or a withdrawal without losing the history."""

    __tablename__ = "applications"
    __table_args__ = (
        CheckConstraint(_in("status", APPLICATION_STATUSES), name="status_valid"),
        CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL)", name="decided_matches_status"
        ),
        # One pending application per student and project: a repeat or a double click finds the
        # existing one instead of adding a second.
        Index(
            "uq_applications_pending",
            "project_id",
            "applicant_id",
            unique=True,
            postgresql_where=text(PENDING_SQL),
        ),
        # The owner's queue reads a project's applications by status, newest first.
        Index("ix_applications_project_status_created", "project_id", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    applicant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project_roles.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
