"""Who may be recommended to whom, decided in SQL before any ranking runs. Ranking only ever
sees rows this filter let through, so no score can bring back someone it removed."""

import uuid

from sqlalchemy import ColumnElement, and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.connections.models import OPEN_STATES, Connection
from app.modules.profiles.models import AvailabilitySlot, Profile
from app.modules.profiles.visibility import visible_to


async def viewer_eligible(session: AsyncSession, viewer_id: uuid.UUID) -> bool:
    """A student who may ask for recommendations: verified, not suspended, a student (not an
    admin), with a profile that has opted in to discovery."""
    found = await session.scalar(
        select(Profile.user_id)
        .join(User, User.id == Profile.user_id)
        .where(
            Profile.user_id == viewer_id,
            Profile.discoverable,
            User.role == "student",
            User.verified_at.is_not(None),
            User.suspended_at.is_(None),
        )
    )
    return found is not None


def _open_connection(viewer_id: uuid.UUID) -> ColumnElement[bool]:
    """True when the candidate and the viewer already have a pending or accepted connection,
    whichever of them sent it."""
    return exists().where(
        or_(
            and_(Connection.sender_id == viewer_id, Connection.recipient_id == Profile.user_id),
            and_(Connection.sender_id == Profile.user_id, Connection.recipient_id == viewer_id),
        ),
        Connection.state.in_(OPEN_STATES),
    )


def _has_availability() -> ColumnElement[bool]:
    """No availability means unavailable: a candidate with nothing to share can't be scheduled
    around, so they're excluded rather than scored down."""
    return exists().where(AvailabilitySlot.user_id == Profile.user_id)


def candidate_filter(viewer_id: uuid.UUID) -> ColumnElement[bool]:
    """Candidates the viewer may be recommended, for a query joining Profile to User: visible to
    the viewer, discoverable, a verified and active student, not the viewer, no block either way,
    no pending or accepted connection either way, and at least one availability slot."""
    return and_(
        visible_to(viewer_id),
        Profile.discoverable,
        User.role == "student",
        Profile.user_id != viewer_id,
        ~_open_connection(viewer_id),
        _has_availability(),
    )
