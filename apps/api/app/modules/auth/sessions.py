from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.models import User, UserSession
from app.modules.auth.tokens import hash_token

# last_seen_at is refreshed at most this often, so reads don't write on every request.
TOUCH_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True)
class CurrentSession:
    session: UserSession
    user: User


async def load_session(
    db: AsyncSession, settings: Settings, raw_cookie: str
) -> CurrentSession | None:
    """The live session for a cookie value, or None. Sessions that expired or belong to a
    suspended account are deleted on sight."""
    return await load_session_by_hash(db, settings, hash_token(raw_cookie))


async def load_session_by_hash(
    db: AsyncSession, settings: Settings, id_hash: str
) -> CurrentSession | None:
    """As load_session, for callers that only kept the hash (sockets re-check it per event)."""
    row = (
        await db.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(UserSession.id_hash == id_hash)
        )
    ).first()
    if row is None:
        return None
    session, user = row

    now = datetime.now(UTC)
    idle_deadline = session.last_seen_at + timedelta(seconds=settings.session_idle_timeout_seconds)
    if session.expires_at <= now or idle_deadline <= now or user.suspended_at is not None:
        await db.delete(session)
        await db.commit()
        return None
    if now - session.last_seen_at > TOUCH_INTERVAL:
        session.last_seen_at = now
        await db.commit()
    return CurrentSession(session=session, user=user)
