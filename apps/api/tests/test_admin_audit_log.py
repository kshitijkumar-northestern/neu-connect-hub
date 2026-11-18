"""The audit log outlives everything it mentions: deleting the accounts and reports a row points
at nulls those ids and keeps the row, however many of them go in one transaction."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import MemoryMailer
from tests.helpers import execute, student, user_id

pytestmark = pytest.mark.usefixtures("clean_database")

ADA = "ada@northeastern.edu"
GRACE = "grace@northeastern.edu"


async def test_several_accounts_can_be_deleted_in_one_transaction(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    """An audit row can point at an admin, a student and a report about that student at once.
    Deleting them together (a bulk purge, the demo seeder) nulls all three ids on the same row in
    one transaction, and the row must survive that rather than fail its foreign key checks."""
    admin_id = await user_id(await student(app, mailer, ADA))
    target_id = await user_id(await student(app, mailer, GRACE))
    reporter_id = await user_id(await student(app, mailer, "alan@northeastern.edu"))
    await execute(clean_database, "UPDATE users SET role = 'admin' WHERE id = :id", id=admin_id)
    report_id = str(uuid.uuid4())
    await execute(
        clean_database,
        "INSERT INTO reports (id, reporter_id, target_user_id, reason, status)"
        " VALUES (:id, :reporter, :target, 'spam', 'resolved')",
        id=report_id,
        reporter=reporter_id,
        target=target_id,
    )
    audit_id = str(uuid.uuid4())
    await execute(
        clean_database,
        "INSERT INTO admin_audit_log (id, admin_id, action, target_user_id, report_id)"
        " VALUES (:id, :admin, 'account_suspended', :target, :report)",
        id=audit_id,
        admin=admin_id,
        target=target_id,
        report=report_id,
    )

    async with clean_database.begin() as connection:
        await connection.execute(text("DELETE FROM users WHERE id = :admin"), {"admin": admin_id})
        await connection.execute(
            text("DELETE FROM users WHERE id = :target"), {"target": target_id}
        )

    async with clean_database.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT admin_id, target_user_id, report_id FROM admin_audit_log WHERE id = :id"
                ),
                {"id": audit_id},
            )
        ).one()
    assert tuple(row) == (None, None, None)


async def test_the_audit_logs_foreign_keys_are_checked_at_commit(
    clean_database: AsyncEngine,
) -> None:
    """Whether the test above trips over its foreign keys depends on the order Postgres fires
    the cascades in, which differs between databases. Checking them at commit, once every
    cascade has nulled what it should, makes the outcome independent of that order."""
    async with clean_database.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT conname, condeferrable, condeferred FROM pg_constraint"
                    " WHERE conrelid = 'admin_audit_log'::regclass AND contype = 'f'"
                    " ORDER BY conname"
                )
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        ("fk_admin_audit_log_admin_id_users", True, True),
        ("fk_admin_audit_log_report_id_reports", True, True),
        ("fk_admin_audit_log_target_user_id_users", True, True),
    ]
