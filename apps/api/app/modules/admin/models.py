import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Every action an administrator (or the grant_admin command) can take. The log stores only the
# action and the ids involved, never free text, so nothing private ends up in it.
AUDIT_ACTIONS = (
    "admin_granted",
    "admin_revoked",
    "mfa_reset",
    "mfa_enrolled",
    "mfa_verified",
    "recovery_code_used",
    "report_viewed",
    "report_dismissed",
    "account_suspended",
)
NONCE_BYTES = 16


class AdminMfa(Base):
    """An administrator's TOTP enrolment. The secret is never stored: it is
    HMAC-SHA256(MFA key, user id + nonce), so a database copy alone reveals nothing. Enrolment is
    pending until the first valid code confirms it. `last_used_step` blocks replaying a code
    within its 30-second step."""

    __tablename__ = "admin_mfa"
    __table_args__ = (
        CheckConstraint(f"octet_length(nonce) = {NONCE_BYTES}", name="nonce_length"),
        CheckConstraint(
            "confirmed_at IS NOT NULL OR last_used_step IS NULL", name="no_use_before_confirm"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    nonce: Mapped[bytes] = mapped_column(LargeBinary(NONCE_BYTES))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_step: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdminRecoveryCode(Base):
    """A single-use code for an administrator who has lost their authenticator. Only its
    SHA-256 is stored; using it is a conditional UPDATE on `used_at IS NULL`."""

    __tablename__ = "admin_recovery_codes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdminAuditLog(Base):
    """One row per administrative action, kept when the accounts and reports it mentions are
    deleted: their ids become NULL. Deliberately no copy of who they were: a deleted account's
    reports are deleted with it, and the log shouldn't keep what the account removal erased."""

    __tablename__ = "admin_audit_log"
    __table_args__ = (
        CheckConstraint(
            f"action IN ({', '.join(repr(a) for a in AUDIT_ACTIONS)})", name="action_valid"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # NULL for actions taken from the command line, or once the admin's account is gone.
    admin_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(32))
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("reports.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
