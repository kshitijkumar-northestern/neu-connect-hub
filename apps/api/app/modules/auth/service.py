import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.mail import EmailMessage
from app.core.rate_limit import Limit, RateLimiter
from app.modules.auth.models import EmailVerification, PasswordReset, User, UserSession
from app.modules.auth.passwords import PasswordService
from app.modules.auth.tokens import hash_token, new_token

# Rate limits (sliding windows). Login attempts are counted *before* the password check, so
# concurrent requests cannot race past a limit, and refunded when the login succeeds.
REGISTER_PER_IP = Limit(max_events=10, window_seconds=3600)
LOGIN_PER_IP = Limit(max_events=30, window_seconds=900)
# Strict lockout per account *and* address, so one address can't lock the owner out elsewhere.
LOGIN_FAILURES_PER_ACCOUNT_AND_IP = Limit(max_events=5, window_seconds=900)
# Looser cap per account, bounding guesses spread across many addresses.
LOGIN_ATTEMPTS_PER_ACCOUNT = Limit(max_events=50, window_seconds=3600)
EMAIL_REQUESTS_PER_IP = Limit(max_events=10, window_seconds=3600)
EMAIL_REQUESTS_PER_ADDRESS = Limit(max_events=3, window_seconds=3600)
TOKEN_SUBMISSIONS_PER_IP = Limit(max_events=30, window_seconds=900)

INVALID_CREDENTIALS = "Email or password is incorrect."
INVALID_LINK = "This link is invalid or has expired. Request a new one."

SingleUseToken = EmailVerification | PasswordReset


@dataclass(frozen=True)
class LoginResult:
    user: User
    session_cookie: str
    csrf_cookie: str
    replaced_session_hash: str | None


class AuthService:
    """Account rules. Methods that email the user return the message instead of sending it, so
    the router can send it after the response and timing never depends on the mail provider."""

    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        passwords: PasswordService,
        limiter: RateLimiter,
        client_ip: str,
    ) -> None:
        self.db = db
        self.settings = settings
        self.passwords = passwords
        self.limiter = limiter
        self.client_ip = client_ip

    # --- registration and verification -----------------------------------------------------

    async def register(self, email: str, password: str) -> EmailMessage | None:
        """Create an unverified account and return its verification email. An existing account
        is left untouched; the caller's response is identical either way."""
        self._limit("register", self.client_ip, REGISTER_PER_IP)
        if email.rsplit("@", 1)[1] not in self.settings.allowed_domains:
            raise AppError(
                422, "email_domain_not_allowed", "Register with your university email address."
            )
        existing = await self._user_by_email(email)
        # Hash on both paths so the response time doesn't reveal whether the account exists.
        password_hash = await self.passwords.hash(password)
        if existing is not None:
            return None

        user = User(email=email, password_hash=password_hash)
        self.db.add(user)
        try:
            await self.db.flush()
        except IntegrityError:  # the same email registered concurrently
            await self.db.rollback()
            return None
        token = await self._issue_verification(user.id)
        await self.db.commit()
        return self._verification_email(email, token)

    async def resend_verification(self, email: str) -> EmailMessage | None:
        self._limit_email_request(email)
        user = await self._user_by_email(email)
        if user is None or user.verified_at is not None:
            return None
        token = await self._issue_verification(user.id)
        await self.db.commit()
        return self._verification_email(email, token)

    async def verify_email(self, token: str) -> None:
        now = datetime.now(UTC)
        record = await self._consume(EmailVerification, token, now)
        user = await self.db.get_one(User, record.user_id)
        if user.verified_at is None:
            user.verified_at = now
        await self.db.commit()

    # --- sessions ---------------------------------------------------------------------------

    async def login(self, email: str, password: str, current_cookie: str | None) -> LoginResult:
        pair = self._pair_key(email)
        self._limit("login-ip", self.client_ip, LOGIN_PER_IP)
        self._limit("login-pair", pair, LOGIN_FAILURES_PER_ACCOUNT_AND_IP)
        self._limit("login-account", email, LOGIN_ATTEMPTS_PER_ACCOUNT)

        user = await self._user_by_email(email)
        verified = await self.passwords.verify(user.password_hash if user else None, password)
        if not verified or user is None:
            raise AppError(401, "invalid_credentials", INVALID_CREDENTIALS)
        self.limiter.reset("login-pair", pair)
        self.limiter.reset("login-account", email)
        # Checked only after the password, so suspension is never revealed to anyone else.
        if user.suspended_at is not None:
            raise AppError(403, "account_suspended", "This account is suspended.")

        if self.passwords.needs_rehash(user.password_hash):
            user.password_hash = await self.passwords.hash(password)

        # A new session ID on every login (no fixation); the browser's previous session ends.
        replaced = hash_token(current_cookie) if current_cookie else None
        if replaced:
            await self.db.execute(delete(UserSession).where(UserSession.id_hash == replaced))
        session_cookie, session_hash = new_token()
        csrf_cookie, csrf_hash = new_token()
        now = datetime.now(UTC)
        lifetime = timedelta(seconds=self.settings.session_absolute_lifetime_seconds)
        self.db.add(
            UserSession(
                id_hash=session_hash,
                user_id=user.id,
                csrf_hash=csrf_hash,
                last_seen_at=now,
                expires_at=now + lifetime,
            )
        )
        await self.db.commit()
        return LoginResult(user, session_cookie, csrf_cookie, replaced)

    async def end_session(self, session: UserSession) -> None:
        await self.db.delete(session)
        await self.db.commit()

    # --- password reset ---------------------------------------------------------------------

    async def request_password_reset(self, email: str) -> EmailMessage | None:
        self._limit_email_request(email)
        user = await self._user_by_email(email)
        if user is None:
            return None
        now = datetime.now(UTC)
        await self._retire_unused(PasswordReset, user.id, now)
        token, digest = new_token()
        ttl = self.settings.password_reset_token_ttl_seconds
        self.db.add(
            PasswordReset(
                user_id=user.id, token_hash=digest, expires_at=now + timedelta(seconds=ttl)
            )
        )
        await self.db.commit()
        link = f"{self.settings.web_origin}/reset-password#token={token}"
        return EmailMessage(
            to=email,
            subject="Reset your NU Connect Hub password",
            text="Someone asked to reset the password for your NU Connect Hub account.\n\n"
            f"Choose a new password within {ttl // 60} minutes:\n\n{link}\n\n"
            "If this wasn't you, ignore this email; your password stays the same.",
        )

    async def confirm_password_reset(self, token: str, password: str) -> uuid.UUID:
        """Set the new password and end every session. Returns the user ID."""
        now = datetime.now(UTC)
        record = await self._consume(PasswordReset, token, now)
        user = await self.db.get_one(User, record.user_id)
        user.password_hash = await self.passwords.hash(password)
        if user.verified_at is None:  # following the emailed link proves the address
            user.verified_at = now
        await self.db.execute(delete(UserSession).where(UserSession.user_id == user.id))
        await self.db.commit()
        self.limiter.reset("login-account", user.email)
        self.limiter.forget("login-pair", f"{user.email}|")
        return user.id

    # --- helpers ----------------------------------------------------------------------------

    async def _consume[T: (EmailVerification, PasswordReset)](
        self, model: type[T], token: str, now: datetime
    ) -> T:
        """Use a single-use link exactly once (row lock), retiring the user's other links."""
        self._limit("token", self.client_ip, TOKEN_SUBMISSIONS_PER_IP)
        record = await self.db.scalar(
            select(model).where(model.token_hash == hash_token(token)).with_for_update()
        )
        if record is None or record.used_at is not None or record.expires_at <= now:
            raise AppError(400, "invalid_token", INVALID_LINK)
        await self._retire_unused(model, record.user_id, now)
        return record

    async def _user_by_email(self, email: str) -> User | None:
        return await self.db.scalar(select(User).where(User.email == email))

    async def _issue_verification(self, user_id: uuid.UUID) -> str:
        now = datetime.now(UTC)
        await self._retire_unused(EmailVerification, user_id, now)
        token, digest = new_token()
        ttl = timedelta(seconds=self.settings.verification_token_ttl_seconds)
        self.db.add(EmailVerification(user_id=user_id, token_hash=digest, expires_at=now + ttl))
        return token

    async def _retire_unused(
        self, model: type[SingleUseToken], user_id: uuid.UUID, now: datetime
    ) -> None:
        """Older links stop working once a newer one is issued or one is used."""
        await self.db.execute(
            update(model)
            .where(model.user_id == user_id, model.used_at.is_(None))
            .values(used_at=now)
        )

    def _verification_email(self, email: str, token: str) -> EmailMessage:
        hours = self.settings.verification_token_ttl_seconds // 3600
        link = f"{self.settings.web_origin}/verify-email#token={token}"
        return EmailMessage(
            to=email,
            subject="Verify your NU Connect Hub email",
            text="Welcome to NU Connect Hub.\n\n"
            f"Confirm your university email address within {hours} hours:\n\n{link}\n\n"
            "If you didn't create an account, you can ignore this email.",
        )

    def _pair_key(self, email: str) -> str:
        return f"{email}|{self.client_ip}"

    def _limit_email_request(self, email: str) -> None:
        self._limit("email-ip", self.client_ip, EMAIL_REQUESTS_PER_IP)
        self._limit("email-address", email, EMAIL_REQUESTS_PER_ADDRESS)

    def _limit(self, bucket: str, key: str, limit: Limit) -> None:
        retry_after = self.limiter.hit(bucket, key, limit)
        if retry_after is not None:
            raise AppError(
                429,
                "rate_limited",
                "Too many attempts. Please wait and try again.",
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            )
