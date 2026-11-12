import math
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limit import Limit, RateLimiter
from app.modules.auth.models import User
from app.modules.connections.models import OPEN_STATES_SQL, Connection
from app.modules.profiles.models import Profile
from app.modules.profiles.visibility import visible_to
from app.modules.scan.models import ACTIVE_SQL, ProfileShareToken
from app.modules.scan.schemas import ConnectionCode, ConnectionState, ConnectPreview
from app.modules.scan.tokens import hash_token, is_well_formed, token_for

ROTATE_PER_STUDENT = Limit(max_events=10, window_seconds=3600)
RESOLVE_PER_STUDENT = Limit(max_events=30, window_seconds=600)

NOT_FOUND = "This code isn't valid."


class ScanService:
    """A student's Scan to Connect code and what a scanner may see through it. Rotating and
    disabling lock the student's `users` row first, so the one-active-code index can never be
    raced by two rotations in flight at once."""

    def __init__(self, db: AsyncSession, settings: Settings, limiter: RateLimiter) -> None:
        self.db = db
        self.settings = settings
        self.limiter = limiter

    async def my_code(self, user_id: uuid.UUID) -> ConnectionCode:
        """The caller's code, as it stands right now. Never creates one. Reads the active row
        directly (there is at most one, by the partial unique index) rather than "the newest",
        so an overlapping rotation can't make a live code look off."""
        row = await self.db.scalar(
            select(ProfileShareToken).where(ProfileShareToken.user_id == user_id, text(ACTIVE_SQL))
        )
        shareable = await self._shareable(user_id)
        if row is None:
            return ConnectionCode(status="off", url=None, expires_at=None, shareable=shareable)
        token = token_for(self.settings.share_token_key, row.id)
        if hash_token(token) != row.token_hash:
            # The signing key no longer matches what was issued: the link is dead, not active.
            return ConnectionCode(status="off", url=None, expires_at=None, shareable=shareable)
        if row.expires_at <= datetime.now(UTC):
            return ConnectionCode(
                status="expired", url=None, expires_at=row.expires_at, shareable=shareable
            )
        return ConnectionCode(
            status="active", url=self._url(token), expires_at=row.expires_at, shareable=shareable
        )

    async def rotate(self, user_id: uuid.UUID) -> ConnectionCode:
        """Revoke whatever is active and issue a fresh code, in one transaction."""
        self._limit("rotate", str(user_id), ROTATE_PER_STUDENT)
        await self.db.execute(select(User.id).where(User.id == user_id).with_for_update())
        await self.db.execute(
            update(ProfileShareToken)
            .where(ProfileShareToken.user_id == user_id, text(ACTIVE_SQL))
            .values(active=False, revoked_at=func.now())
        )
        row_id = uuid.uuid4()
        expires_at = datetime.now(UTC) + timedelta(seconds=self.settings.share_token_ttl_seconds)
        token = token_for(self.settings.share_token_key, row_id)
        self.db.add(
            ProfileShareToken(
                id=row_id, user_id=user_id, token_hash=hash_token(token), expires_at=expires_at
            )
        )
        await self.db.commit()
        return ConnectionCode(
            status="active",
            url=self._url(token),
            expires_at=expires_at,
            shareable=await self._shareable(user_id),
        )

    async def disable(self, user_id: uuid.UUID) -> None:
        """Turns the code off. Idempotent: nothing active is a no-op, not an error."""
        await self.db.execute(select(User.id).where(User.id == user_id).with_for_update())
        await self.db.execute(
            update(ProfileShareToken)
            .where(ProfileShareToken.user_id == user_id, text(ACTIVE_SQL))
            .values(active=False, revoked_at=func.now())
        )
        await self.db.commit()

    async def resolve(self, viewer_id: uuid.UUID, token: object) -> ConnectPreview:
        """Every failure short of success answers with the identical 404, so a scan never
        reveals which reason it failed for."""
        self._limit("resolve", str(viewer_id), RESOLVE_PER_STUDENT)
        if not is_well_formed(token):
            raise self._not_found()
        row = await self.db.scalar(
            select(ProfileShareToken).where(ProfileShareToken.token_hash == hash_token(token))
        )
        if (
            row is None
            or not row.active
            or row.expires_at <= datetime.now(UTC)
            or row.user_id == viewer_id
        ):
            raise self._not_found()
        profile = await self.db.scalar(
            select(Profile)
            .join(User, User.id == Profile.user_id)
            .where(Profile.user_id == row.user_id, visible_to(viewer_id))
        )
        if profile is None:
            raise self._not_found()
        return ConnectPreview(
            user_id=row.user_id,
            display_name=profile.display_name,
            headline=profile.headline,
            connection_state=await self._connection_state(viewer_id, row.user_id),
        )

    # --- helpers ----------------------------------------------------------------------------

    async def _shareable(self, user_id: uuid.UUID) -> bool:
        """Whether a stranger's scan would show the profile: the same conditions `visible_to`
        applies to someone who isn't connected. A student already connected to the owner can
        still open a non-shareable code (visibility there also allows `connected`)."""
        return (
            await self.db.scalar(
                select(Profile.user_id)
                .join(User, User.id == Profile.user_id)
                .where(
                    Profile.user_id == user_id,
                    Profile.discoverable,
                    User.verified_at.is_not(None),
                    User.suspended_at.is_(None),
                )
            )
        ) is not None

    async def _connection_state(self, viewer_id: uuid.UUID, owner_id: uuid.UUID) -> ConnectionState:
        """From the viewer's side: sent -> pending_outgoing, received -> pending_incoming."""
        found = (
            await self.db.execute(
                select(Connection.state, Connection.sender_id).where(
                    or_(
                        and_(
                            Connection.sender_id == viewer_id, Connection.recipient_id == owner_id
                        ),
                        and_(
                            Connection.sender_id == owner_id, Connection.recipient_id == viewer_id
                        ),
                    ),
                    text(OPEN_STATES_SQL),
                )
            )
        ).first()
        if found is None:
            return "none"
        state, sender_id = found
        if state == "accepted":
            return "connected"
        return "pending_outgoing" if sender_id == viewer_id else "pending_incoming"

    def _url(self, token: str) -> str:
        return f"{self.settings.web_origin}/connect#token={token}"

    def _not_found(self) -> AppError:
        return AppError(404, "not_found", NOT_FOUND)

    def _limit(self, bucket: str, key: str, limit: Limit) -> None:
        retry_after = self.limiter.hit(bucket, key, limit)
        if retry_after is not None:
            raise AppError(
                429,
                "rate_limited",
                "Too many attempts. Please wait and try again.",
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            )
