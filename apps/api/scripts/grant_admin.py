"""Grant, revoke or reset administrator access from the command line. This is the only way an
account becomes an administrator: there is no API route for it.

Usage: cd apps/api && python -m scripts.grant_admin <email> [--revoke | --reset-mfa]
"""

import argparse
import asyncio
import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401 - registers every mapped model, so FKs like reports resolve
from app.core.config import Settings, get_settings
from app.db.session import create_database
from app.modules.admin import audit
from app.modules.admin.models import AdminMfa, AdminRecoveryCode
from app.modules.auth.models import User, UserSession

NOT_FOUND = "No account with that email address."
NOT_VERIFIED = "That account hasn't verified its email address yet."


async def grant(settings: Settings, email: str) -> None:
    """Make the account an administrator. Audited with no admin_id, since the grant happens
    outside any session. Running it again on an existing admin is harmless."""
    database = create_database(settings)
    try:
        async with database.sessionmaker() as session:
            user = await session.scalar(select(User).where(User.email == email))
            if user is None:
                print(NOT_FOUND)
                raise SystemExit(1)
            if user.verified_at is None:
                print(NOT_VERIFIED)
                raise SystemExit(1)
            user.role = "admin"
            audit.record(session, "admin_granted", admin_id=None, target_user_id=user.id)
            await session.commit()
    finally:
        await database.engine.dispose()


async def _clear_mfa(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Remove the enrolment and recovery codes, and un-verify every session for this account in
    the same transaction: a session that passed MFA with the old (possibly stolen) device must
    stop working at once, not linger for up to ADMIN_MFA_MAX_AGE_SECONDS."""
    await session.execute(delete(AdminMfa).where(AdminMfa.user_id == user_id))
    await session.execute(delete(AdminRecoveryCode).where(AdminRecoveryCode.user_id == user_id))
    await session.execute(
        update(UserSession).where(UserSession.user_id == user_id).values(mfa_verified_at=None)
    )


async def revoke(settings: Settings, email: str) -> None:
    """Return the account to a student and remove its MFA enrolment and recovery codes."""
    database = create_database(settings)
    try:
        async with database.sessionmaker() as session:
            user = await session.scalar(select(User).where(User.email == email))
            if user is None:
                print(NOT_FOUND)
                raise SystemExit(1)
            user.role = "student"
            await _clear_mfa(session, user.id)
            audit.record(session, "admin_revoked", admin_id=None, target_user_id=user.id)
            await session.commit()
    finally:
        await database.engine.dispose()


async def reset_mfa(settings: Settings, email: str) -> None:
    """Clear a lost authenticator's enrolment and recovery codes, so the admin can enrol again
    at their next visit to /admin. Leaves the account's role untouched."""
    database = create_database(settings)
    try:
        async with database.sessionmaker() as session:
            user = await session.scalar(select(User).where(User.email == email))
            if user is None:
                print(NOT_FOUND)
                raise SystemExit(1)
            await _clear_mfa(session, user.id)
            audit.record(session, "mfa_reset", admin_id=None, target_user_id=user.id)
            await session.commit()
    finally:
        await database.engine.dispose()


def main(argv: list[str] | None = None) -> None:
    """Entry point for the grant_admin command."""
    parser = argparse.ArgumentParser(
        description="Grant, revoke or reset administrator access for an account."
    )
    parser.add_argument("email", help="The account's email address.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--revoke", action="store_true", help="Return the account to a student.")
    group.add_argument(
        "--reset-mfa",
        action="store_true",
        help="Clear a lost authenticator's enrolment and recovery codes.",
    )
    args = parser.parse_args(argv)
    email = args.email.strip().lower()
    settings = get_settings()

    if args.revoke:
        asyncio.run(revoke(settings, email))
        print(f"Revoked administrator access for {email}.")
    elif args.reset_mfa:
        asyncio.run(reset_mfa(settings, email))
        print(f"Cleared MFA enrolment for {email}.")
    else:
        asyncio.run(grant(settings, email))
        print(f"Granted administrator access to {email}.")
        print("They must enrol in MFA at their next visit to /admin.")


if __name__ == "__main__":
    main()
