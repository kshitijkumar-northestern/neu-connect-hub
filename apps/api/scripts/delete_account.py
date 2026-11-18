"""Erase an account from the command line, for a student who asks for it but can't sign in to
delete it themselves. It runs the same erasure as DELETE /me, without the password check, so
the result is identical: every row tied to the account goes, their messages become tombstones,
and reports they filed and audit rows that name them stay with the id cleared.

Usage: cd apps/api && python -m scripts.delete_account (--email <address> | --user-id <id>) --yes

Nothing happens without --yes, in any environment. The address is never printed or logged, only
the account id, so the output can be pasted into the support ticket. --user-id re-applies a
deletion after a database restore, from the ids the "Account deleted" log lines carry; an id
that's already gone exits 1 and changes nothing. A running API refuses the student's open
sockets at their next event, since every event re-checks a session that no longer exists.
"""

import argparse
import asyncio
import sys
import uuid
from typing import NoReturn

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select

import app.db.models  # noqa: F401 - registers every mapped model, so FKs like reports resolve
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import create_database
from app.modules.auth.models import User
from app.modules.auth.schemas import NormalizedEmail
from app.modules.auth.service import erase_account

NOT_FOUND = "No such account."
NEEDS_YES = "refusing to delete without --yes: the deletion can't be undone"
BAD_ARGUMENTS = "invalid arguments (run with --help for usage)"
BAD_EMAIL = "--email isn't a valid email address"

# The type registration and sign-in read addresses with, so a lookup here always matches.
EMAIL = TypeAdapter(NormalizedEmail)


async def delete(
    settings: Settings, *, email: str | None = None, user_id: uuid.UUID | None = None
) -> uuid.UUID:
    """Erase the account with this (already normalised) address or id, and return its id."""
    database = create_database(settings)
    try:
        async with database.sessionmaker() as session:
            match = User.email == email if email is not None else User.id == user_id
            found = await session.scalar(select(User.id).where(match))
            if found is None or not await erase_account(session, found):
                print(NOT_FOUND)
                raise SystemExit(1)
    finally:
        await database.engine.dispose()
    return found


class _Parser(argparse.ArgumentParser):
    """argparse quotes what was typed in its errors ("unrecognized arguments: ada@..."), which
    could be an address: every usage error here is one generic line instead."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {BAD_ARGUMENTS}\n")

    def refuse(self, message: str) -> NoReturn:
        """A usage error whose message is fixed text, never anything typed."""
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}\n")


def _account_id(value: str) -> uuid.UUID:
    """An id, rejected without echoing the value back, in case it was an address."""
    try:
        return uuid.UUID(value)
    except ValueError:
        raise argparse.ArgumentTypeError("not an account id") from None


def main(argv: list[str] | None = None) -> None:
    """Entry point for the delete_account command."""
    parser = _Parser(
        description="Erase an account and everything tied to it. This can't be undone."
    )
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--email", help="The account's email address.")
    which.add_argument("--user-id", type=_account_id, help="The account's id.")
    parser.add_argument("--yes", action="store_true", help="Confirm the deletion.")
    args = parser.parse_args(argv)
    if not args.yes:
        parser.refuse(NEEDS_YES)
    email: str | None = None
    if args.email is not None:
        try:
            email = EMAIL.validate_python(args.email)
        except ValidationError:
            # The validation message quotes the input: never print it.
            parser.refuse(BAD_EMAIL)
    settings = get_settings()
    configure_logging(settings)
    if email is not None:
        deleted = asyncio.run(delete(settings, email=email))
    else:
        deleted = asyncio.run(delete(settings, user_id=args.user_id))
    print(f"Deleted account {deleted}.")


if __name__ == "__main__":
    main()
