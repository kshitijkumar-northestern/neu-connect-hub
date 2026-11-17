"""The CLI runs its own event loop (`asyncio.run` inside `main`), so every call here goes
through a worker thread: calling it directly from one of these `async def` tests would collide
with the loop pytest-asyncio is already running."""

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import Sequence

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from scripts.grant_admin import NOT_FOUND, NOT_VERIFIED, main
from tests.conftest import API_ROOT, TEST_DATABASE_URL, TEST_SESSION_SECRET, MemoryMailer
from tests.helpers import execute, scalar, student, user_id

pytestmark = pytest.mark.usefixtures("clean_database")

ADA = "ada@northeastern.edu"
UNKNOWN = "nobody@northeastern.edu"
NONCE = b"0123456789abcdef"  # exactly 16 bytes, as admin_mfa requires


async def run(argv: Sequence[str]) -> None:
    await asyncio.to_thread(main, list(argv))


async def role_of(engine: AsyncEngine, email: str) -> str:
    return str(await scalar(engine, "SELECT role FROM users WHERE email = :e", e=email))


async def audit_rows(engine: AsyncEngine, action: str, target_id: str) -> int:
    return int(
        str(
            await scalar(
                engine,
                "SELECT count(*) FROM admin_audit_log"
                " WHERE action = :a AND target_user_id = :t AND admin_id IS NULL",
                a=action,
                t=target_id,
            )
        )
    )


async def seed_mfa(engine: AsyncEngine, user: str) -> None:
    await execute(
        engine,
        "INSERT INTO admin_mfa (user_id, nonce, confirmed_at, last_used_step)"
        " VALUES (:u, :n, now(), 1)",
        u=user,
        n=NONCE,
    )
    await execute(
        engine,
        "INSERT INTO admin_recovery_codes (id, user_id, code_hash) VALUES (:id, :u, :h)",
        id=str(uuid.uuid4()),
        u=user,
        h="a" * 64,
    )


async def test_granting_makes_a_verified_student_an_admin(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    client = await student(app, mailer, ADA)
    ada_id = await user_id(client)
    await client.aclose()

    await run([ADA])

    assert await role_of(clean_database, ADA) == "admin"
    assert await audit_rows(clean_database, "admin_granted", ada_id) == 1


async def test_granting_is_idempotent(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    client = await student(app, mailer, ADA)
    ada_id = await user_id(client)
    await client.aclose()

    await run([ADA])
    await run([ADA])  # running it again doesn't error, and the account stays an admin

    assert await role_of(clean_database, ADA) == "admin"
    assert await audit_rows(clean_database, "admin_granted", ada_id) == 2


async def test_granting_an_unknown_email_exits_with_an_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        await run([UNKNOWN])
    assert exc.value.code == 1
    assert NOT_FOUND in capsys.readouterr().out


async def test_granting_an_unverified_account_exits_with_an_error(
    app: object, mailer: MemoryMailer, capsys: pytest.CaptureFixture[str]
) -> None:
    client = await student(app, mailer, ADA, verified=False)
    await client.aclose()

    with pytest.raises(SystemExit) as exc:
        await run([ADA])
    assert exc.value.code == 1
    assert NOT_VERIFIED in capsys.readouterr().out


async def test_revoking_returns_the_account_to_a_student_and_clears_mfa(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    client = await student(app, mailer, ADA)
    ada_id = await user_id(client)
    await client.aclose()
    await run([ADA])
    await seed_mfa(clean_database, ada_id)

    await run([ADA, "--revoke"])

    assert await role_of(clean_database, ADA) == "student"
    assert (
        await scalar(clean_database, "SELECT count(*) FROM admin_mfa WHERE user_id = :u", u=ada_id)
        == 0
    )
    assert (
        await scalar(
            clean_database, "SELECT count(*) FROM admin_recovery_codes WHERE user_id = :u", u=ada_id
        )
        == 0
    )
    assert await audit_rows(clean_database, "admin_revoked", ada_id) == 1


async def test_revoking_an_unknown_email_exits_with_an_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        await run([UNKNOWN, "--revoke"])
    assert exc.value.code == 1
    assert NOT_FOUND in capsys.readouterr().out


async def test_reset_mfa_clears_enrolment_but_keeps_the_admin_role(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    client = await student(app, mailer, ADA)
    ada_id = await user_id(client)
    await client.aclose()
    await run([ADA])
    await seed_mfa(clean_database, ada_id)

    await run([ADA, "--reset-mfa"])

    assert await role_of(clean_database, ADA) == "admin"
    assert (
        await scalar(clean_database, "SELECT count(*) FROM admin_mfa WHERE user_id = :u", u=ada_id)
        == 0
    )
    assert (
        await scalar(
            clean_database, "SELECT count(*) FROM admin_recovery_codes WHERE user_id = :u", u=ada_id
        )
        == 0
    )
    assert await audit_rows(clean_database, "mfa_reset", ada_id) == 1


async def test_reset_mfa_on_an_unknown_email_exits_with_an_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        await run([UNKNOWN, "--reset-mfa"])
    assert exc.value.code == 1
    assert NOT_FOUND in capsys.readouterr().out


async def test_revoke_and_reset_mfa_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit) as exc:
        await run([ADA, "--revoke", "--reset-mfa"])
    assert exc.value.code == 2  # argparse's own usage error


# --- as an operator would actually run it: a fresh interpreter, nothing pre-imported ----------
#
# Calling `main()` in-process (above) only works because pytest already imported the whole app
# (conftest -> app.main), which registers every mapped model. A real operator runs
# `python -m scripts.grant_admin ...` in a brand new process, where the script's own imports are
# all SQLAlchemy's mapper configuration has to go on.


def run_cli(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "DATABASE_URL": TEST_DATABASE_URL,
        "SESSION_SECRET": TEST_SESSION_SECRET,
        "APP_ENV": "test",
    }
    return subprocess.run(  # noqa: S603 - fixed module name and sys.executable, not user input
        [sys.executable, "-m", "scripts.grant_admin", *argv],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


async def test_granting_works_from_a_fresh_interpreter(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    client = await student(app, mailer, ADA)
    ada_id = await user_id(client)
    await client.aclose()

    result = run_cli([ADA])

    assert result.returncode == 0, result.stderr
    assert await role_of(clean_database, ADA) == "admin"
    assert await audit_rows(clean_database, "admin_granted", ada_id) == 1


async def test_reset_mfa_works_from_a_fresh_interpreter(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    client = await student(app, mailer, ADA)
    ada_id = await user_id(client)
    await client.aclose()
    assert run_cli([ADA]).returncode == 0
    await seed_mfa(clean_database, ada_id)

    result = run_cli([ADA, "--reset-mfa"])

    assert result.returncode == 0, result.stderr
    assert await role_of(clean_database, ADA) == "admin"
    assert (
        await scalar(clean_database, "SELECT count(*) FROM admin_mfa WHERE user_id = :u", u=ada_id)
        == 0
    )
    assert await audit_rows(clean_database, "mfa_reset", ada_id) == 1


async def test_revoking_works_from_a_fresh_interpreter(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    client = await student(app, mailer, ADA)
    ada_id = await user_id(client)
    await client.aclose()
    assert run_cli([ADA]).returncode == 0

    result = run_cli([ADA, "--revoke"])

    assert result.returncode == 0, result.stderr
    assert await role_of(clean_database, ADA) == "student"
    assert await audit_rows(clean_database, "admin_revoked", ada_id) == 1


# --- a stolen device must stop working immediately, not after up to ADMIN_MFA_MAX_AGE_SECONDS ---


async def mfa_verified_at(engine: AsyncEngine, user: str) -> object:
    return await scalar(engine, "SELECT mfa_verified_at FROM sessions WHERE user_id = :id", id=user)


async def test_reset_mfa_revokes_every_sessions_mfa_verification(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    client = await student(app, mailer, ADA)
    try:
        ada_id = await user_id(client)
        await execute(clean_database, "UPDATE users SET role = 'admin' WHERE id = :id", id=ada_id)
        await execute(
            clean_database,
            "UPDATE sessions SET mfa_verified_at = now() WHERE user_id = :id",
            id=ada_id,
        )
        assert (await client.get("/api/v1/admin/reports")).status_code == 200

        result = run_cli([ADA, "--reset-mfa"])
        assert result.returncode == 0, result.stderr

        response = await client.get("/api/v1/admin/reports")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "mfa_required"
        assert await mfa_verified_at(clean_database, ada_id) is None
    finally:
        await client.aclose()


async def test_revoking_also_revokes_every_sessions_mfa_verification(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    client = await student(app, mailer, ADA)
    try:
        ada_id = await user_id(client)
        await execute(clean_database, "UPDATE users SET role = 'admin' WHERE id = :id", id=ada_id)
        await execute(
            clean_database,
            "UPDATE sessions SET mfa_verified_at = now() WHERE user_id = :id",
            id=ada_id,
        )
        assert (await client.get("/api/v1/admin/reports")).status_code == 200

        result = run_cli([ADA, "--revoke"])
        assert result.returncode == 0, result.stderr

        # the role is gone, so the student-facing 404 shows up first either way
        assert (await client.get("/api/v1/admin/reports")).status_code == 404
        # cleared regardless, so a later re-grant can't inherit a stale verification
        assert await mfa_verified_at(clean_database, ada_id) is None
    finally:
        await client.aclose()
