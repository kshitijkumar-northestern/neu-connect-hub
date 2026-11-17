"""Administrator MFA: enrolment, confirmation and step-up verification."""

import math
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limit import Limit, RateLimiter
from app.core.security import mfa_is_recent
from app.modules.admin.audit import record
from app.modules.admin.mfa_schemas import ConfirmResponse, EnrollResponse, MfaStatus
from app.modules.admin.models import NONCE_BYTES, AdminMfa, AdminRecoveryCode
from app.modules.admin.recovery import generate_codes, hash_code
from app.modules.admin.totp import derive_secret, matching_step, provisioning_uri, to_base32
from app.modules.auth.models import User, UserSession

# Wrong, replayed or used codes all answer the same way, so guessing never learns which it was.
ATTEMPTS_PER_ADMIN = Limit(max_events=5, window_seconds=300)

ALREADY_ENROLLED = (
    "You've already set up an authenticator. Use a recovery code, then ask for a reset."
)
NOT_PENDING = "There's nothing to confirm. Start enrolment again."
NOT_ENROLLED = "Set up an authenticator before verifying."
INVALID_CODE = "That code isn't right."


class MfaService:
    """TOTP enrolment and step-up verification for one admin at a time."""

    def __init__(self, db: AsyncSession, settings: Settings, limiter: RateLimiter) -> None:
        self.db = db
        self.settings = settings
        self.limiter = limiter

    async def status(self, user: User, session: UserSession) -> MfaStatus:
        mfa = await self._mfa_row(user.id)
        return MfaStatus(
            enrolled=mfa is not None and mfa.confirmed_at is not None,
            verified=mfa_is_recent(session, self.settings),
            recovery_codes_left=await self._recovery_codes_left(user.id),
        )

    async def enroll(self, user: User) -> EnrollResponse:
        """Creates or replaces a pending enrolment. A confirmed one must be reset first (by a
        recovery code and then the CLI), so a stolen session alone can't swap out a real admin's
        authenticator."""
        existing = await self.db.scalar(
            select(AdminMfa).where(AdminMfa.user_id == user.id).with_for_update()
        )
        if existing is not None and existing.confirmed_at is not None:
            raise AppError(409, "mfa_already_enrolled", ALREADY_ENROLLED)
        nonce = secrets.token_bytes(NONCE_BYTES)
        if existing is None:
            self.db.add(AdminMfa(user_id=user.id, nonce=nonce))
        else:
            existing.nonce = nonce
        await self.db.commit()
        secret = derive_secret(self.settings.mfa_key, user.id, nonce)
        return EnrollResponse(
            secret=to_base32(secret), otpauth_url=provisioning_uri(secret, user.email)
        )

    async def confirm(self, user: User, session: UserSession, code: str) -> ConfirmResponse:
        """One transaction: verify the pending secret, confirm it, replace the recovery codes
        and mark this session verified."""
        self._guard_throttle(user.id)
        now = datetime.now(UTC)
        mfa = await self.db.scalar(
            select(AdminMfa).where(AdminMfa.user_id == user.id).with_for_update()
        )
        if mfa is None or mfa.confirmed_at is not None:
            raise AppError(409, "mfa_not_pending", NOT_PENDING)
        secret = derive_secret(self.settings.mfa_key, user.id, mfa.nonce)
        step = matching_step(secret, code, now)
        if step is None:
            self._record_failed_attempt(user.id)
            raise AppError(422, "invalid_code", INVALID_CODE)
        confirmed = await self.db.scalar(
            update(AdminMfa)
            .where(AdminMfa.user_id == user.id, AdminMfa.confirmed_at.is_(None))
            .values(confirmed_at=now, last_used_step=step)
            .returning(AdminMfa.user_id)
        )
        if confirmed is None:  # confirmed by a concurrent request in the moment between
            await self.db.rollback()
            raise AppError(409, "mfa_not_pending", NOT_PENDING)
        codes = generate_codes()
        await self.db.execute(delete(AdminRecoveryCode).where(AdminRecoveryCode.user_id == user.id))
        self.db.add_all(
            AdminRecoveryCode(user_id=user.id, code_hash=hash_code(plain)) for plain in codes
        )
        session.mfa_verified_at = now
        record(self.db, "mfa_enrolled", admin_id=user.id)
        await self.db.commit()
        return ConfirmResponse(recovery_codes=codes)

    async def verify(
        self, user: User, session: UserSession, code: str | None, recovery_code: str | None
    ) -> MfaStatus:
        self._guard_throttle(user.id)
        now = datetime.now(UTC)
        mfa = await self.db.scalar(
            select(AdminMfa).where(AdminMfa.user_id == user.id, AdminMfa.confirmed_at.is_not(None))
        )
        if mfa is None:
            raise AppError(409, "mfa_not_enrolled", NOT_ENROLLED)
        if code is not None:
            await self._verify_code(user.id, mfa, code, now)
        elif recovery_code is not None:
            await self._use_recovery_code(user.id, recovery_code, now)
        else:
            # VerifyRequest.extra="forbid" plus its "exactly one" validator rule this out.
            raise AppError(422, "invalid_code", INVALID_CODE)
        session.mfa_verified_at = now
        await self.db.commit()
        return await self.status(user, session)

    # --- helpers ----------------------------------------------------------------------------

    async def _verify_code(
        self, user_id: uuid.UUID, mfa: AdminMfa, code: str, now: datetime
    ) -> None:
        secret = derive_secret(self.settings.mfa_key, user_id, mfa.nonce)
        step = matching_step(secret, code, now)
        if step is None:
            self._record_failed_attempt(user_id)
            raise AppError(422, "invalid_code", INVALID_CODE)
        # The only guard against replay: one conditional UPDATE, so two concurrent checks for
        # the same step can't both succeed, and an older step can never win over a newer one.
        accepted = await self.db.scalar(
            update(AdminMfa)
            .where(
                AdminMfa.user_id == user_id,
                AdminMfa.confirmed_at.is_not(None),
                or_(AdminMfa.last_used_step.is_(None), AdminMfa.last_used_step < step),
            )
            .values(last_used_step=step)
            .returning(AdminMfa.user_id)
        )
        if accepted is None:
            await self.db.rollback()
            self._record_failed_attempt(user_id)
            raise AppError(422, "invalid_code", INVALID_CODE)
        record(self.db, "mfa_verified", admin_id=user_id)

    async def _use_recovery_code(
        self, user_id: uuid.UUID, recovery_code: str, now: datetime
    ) -> None:
        # One conditional UPDATE on `used_at IS NULL`: concurrent uses of the same code can only
        # let one through.
        used = await self.db.scalar(
            update(AdminRecoveryCode)
            .where(
                AdminRecoveryCode.user_id == user_id,
                AdminRecoveryCode.code_hash == hash_code(recovery_code),
                AdminRecoveryCode.used_at.is_(None),
            )
            .values(used_at=now)
            .returning(AdminRecoveryCode.id)
        )
        if used is None:
            await self.db.rollback()
            self._record_failed_attempt(user_id)
            raise AppError(422, "invalid_code", INVALID_CODE)
        record(self.db, "recovery_code_used", admin_id=user_id)

    async def _mfa_row(self, user_id: uuid.UUID) -> AdminMfa | None:
        return await self.db.scalar(select(AdminMfa).where(AdminMfa.user_id == user_id))

    async def _recovery_codes_left(self, user_id: uuid.UUID) -> int:
        count = await self.db.scalar(
            select(func.count())
            .select_from(AdminRecoveryCode)
            .where(AdminRecoveryCode.user_id == user_id, AdminRecoveryCode.used_at.is_(None))
        )
        return count or 0

    def _guard_throttle(self, user_id: uuid.UUID) -> None:
        """Refuses the request before it does anything else when the budget is already spent.
        Checking (not recording) here means a correct code is still refused once the admin has
        used up their failures, but never consumes budget on its own."""
        retry_after = self.limiter.check("admin-mfa-attempt", str(user_id), ATTEMPTS_PER_ADMIN)
        if retry_after is not None:
            raise AppError(
                429,
                "rate_limited",
                "Too many attempts. Please wait and try again.",
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            )

    def _record_failed_attempt(self, user_id: uuid.UUID) -> None:
        """A wrong, replayed or used code counts against the shared budget; a correct one never
        does, so repeated successful sign-ins (several tabs, several sessions) can't lock an
        admin out of their own account."""
        self.limiter.hit("admin-mfa-attempt", str(user_id), ATTEMPTS_PER_ADMIN)
