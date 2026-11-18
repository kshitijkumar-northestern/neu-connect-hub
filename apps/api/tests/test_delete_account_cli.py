"""The operator's deletion command, for a student who asks for their account to be erased but
can't sign in to do it themselves. Like test_grant_admin, every in-process call goes through a
worker thread, because `main` runs its own event loop."""

import asyncio
import logging
import os
import subprocess
import sys
from collections.abc import Sequence

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from scripts.delete_account import NOT_FOUND, main
from tests.conftest import API_ROOT, TEST_DATABASE_URL, TEST_SESSION_SECRET, MemoryMailer
from tests.helpers import scalar, student_with_profile, user_id
from tests.test_account_deletion import insert_message, rows_referencing

pytestmark = [
    pytest.mark.usefixtures("clean_database"),
    pytest.mark.filterwarnings("error::sqlalchemy.exc.SAWarning"),
]

ADA = "ada@northeastern.edu"
GRACE = "grace@northeastern.edu"
UNKNOWN = "nobody@northeastern.edu"


async def run(argv: Sequence[str]) -> None:
    await asyncio.to_thread(main, list(argv))


def run_cli(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """As an operator runs it: a fresh interpreter, where only the script's own imports
    register the mapped models."""
    env = {
        **os.environ,
        "DATABASE_URL": TEST_DATABASE_URL,
        "SESSION_SECRET": TEST_SESSION_SECRET,
        "APP_ENV": "test",
    }
    return subprocess.run(  # noqa: S603 - fixed module name and sys.executable, not user input
        [sys.executable, "-m", "scripts.delete_account", *argv],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


async def accounts(engine: AsyncEngine, email: str) -> int:
    return int(str(await scalar(engine, "SELECT count(*) FROM users WHERE email = :e", e=email)))


async def ada_and_grace(app: object, mailer: MemoryMailer) -> tuple[httpx.AsyncClient, str, str]:
    """Ada, with a profile, a share code and a conversation with Grace that has a message from
    each of them. Returns Ada's client, Ada's id and the conversation id."""
    ada = await student_with_profile(app, mailer, ADA)
    grace = await student_with_profile(app, mailer, GRACE)
    try:
        ada_id, grace_id = await user_id(ada), await user_id(grace)
        assert (await ada.post("/api/v1/me/connection-code/rotate")).status_code == 201
        sent = (await ada.post("/api/v1/connections", json={"user_id": grace_id})).json()
        accepted = (await grace.post(f"/api/v1/connections/{sent['id']}/accept")).json()
    finally:
        await grace.aclose()
    return ada, ada_id, str(accepted["conversation_id"])


async def test_deleting_erases_the_account_the_way_self_service_deletion_does(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada, ada_id, conversation_id = await ada_and_grace(app, mailer)
    message_id = await insert_message(clean_database, conversation_id, ada_id, "Ada's message")
    try:
        await run(["--email", ADA, "--yes"])

        after = await rows_referencing(clean_database, ada_id)
        assert after == dict.fromkeys(after, 0)
        assert await accounts(clean_database, ADA) == 0
        # The same path as DELETE /me: her message to Grace becomes a tombstone.
        assert (
            await scalar(clean_database, "SELECT body FROM messages WHERE id = :id", id=message_id)
            is None
        )
        assert (
            await scalar(
                clean_database,
                "SELECT removed_at IS NOT NULL FROM messages WHERE id = :id",
                id=message_id,
            )
            is True
        )
        # Every session of hers is gone with the account.
        assert (await ada.get("/api/v1/me")).status_code == 401
    finally:
        await ada.aclose()


async def test_the_address_is_matched_the_way_sign_in_matches_it(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada, _, _ = await ada_and_grace(app, mailer)
    await ada.aclose()

    await run(["--email", "  Ada@Northeastern.EDU ", "--yes"])

    assert await accounts(clean_database, ADA) == 0


async def test_an_address_pasted_with_a_name_matches_the_way_registration_reads_it(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    """Registration and sign-in read addresses through one type; the command uses it too, so
    whatever form an address arrives in from a support ticket finds the same account."""
    ada, _, _ = await ada_and_grace(app, mailer)
    await ada.aclose()

    await run(["--email", "Ada Lovelace <Ada@Northeastern.edu>", "--yes"])

    assert await accounts(clean_database, ADA) == 0


async def test_an_invalid_address_is_refused_without_echoing_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        await run(["--email", "ada.northeastern.edu", "--yes"])

    assert exc.value.code == 2
    out = capsys.readouterr()
    assert "ada.northeastern.edu" not in out.out + out.err


async def test_stray_arguments_are_refused_without_echoing_them(
    app: object,
    mailer: MemoryMailer,
    capsys: pytest.CaptureFixture[str],
    clean_database: AsyncEngine,
) -> None:
    ada, _, _ = await ada_and_grace(app, mailer)
    await ada.aclose()

    with pytest.raises(SystemExit) as exc:
        await run(["--email", ADA, "--yes", GRACE])

    assert exc.value.code == 2
    out = capsys.readouterr()
    assert ADA not in out.out + out.err
    assert GRACE not in out.out + out.err
    assert await accounts(clean_database, ADA) == 1


async def test_without_yes_it_refuses_and_deletes_nothing(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada, _, _ = await ada_and_grace(app, mailer)
    await ada.aclose()

    with pytest.raises(SystemExit) as exc:
        await run(["--email", ADA])

    assert exc.value.code == 2
    assert await accounts(clean_database, ADA) == 1


async def test_an_unknown_address_exits_with_an_error_and_never_echoes_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        await run(["--email", UNKNOWN, "--yes"])

    assert exc.value.code == 1
    out = capsys.readouterr()
    assert NOT_FOUND in out.out
    assert UNKNOWN not in out.out + out.err


async def test_the_log_names_the_account_by_id_only(
    app: object,
    mailer: MemoryMailer,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    ada, ada_id, _ = await ada_and_grace(app, mailer)
    await ada.aclose()

    with caplog.at_level(logging.INFO, logger="app"):
        await run(["--email", ADA, "--yes"])

    deletions = [record for record in caplog.records if record.name == "app.accounts"]
    assert [getattr(record, "user_id", None) for record in deletions] == [ada_id]
    for record in caplog.records:
        assert ADA not in record.getMessage()
        assert ADA not in str(vars(record))
    out = capsys.readouterr()
    assert ada_id in out.out
    assert ADA not in out.out + out.err


async def test_a_deletion_can_be_reapplied_by_id_after_a_restore(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    """After a restore, the ids logged since the backup are all the operator has: deleting by
    id erases the account, and running it again for one that's already gone changes nothing."""
    ada, ada_id, _ = await ada_and_grace(app, mailer)
    await ada.aclose()

    await run(["--user-id", ada_id, "--yes"])
    assert await accounts(clean_database, ADA) == 0

    with pytest.raises(SystemExit) as exc:
        await run(["--user-id", ada_id, "--yes"])
    assert exc.value.code == 1


async def test_email_and_id_are_one_or_the_other(capsys: pytest.CaptureFixture[str]) -> None:
    for argv in (
        ["--email", ADA, "--user-id", "0b7a3f9e-4c1d-4e5f-8a6b-7c8d9e0f1a2b", "--yes"],
        ["--yes"],
        ["--user-id", ADA, "--yes"],
    ):
        with pytest.raises(SystemExit) as exc:
            await run(argv)
        assert exc.value.code == 2, argv
    # Even a mistyped flag never echoes the address back.
    out = capsys.readouterr()
    assert ADA not in out.out + out.err


async def test_deleting_works_from_a_fresh_interpreter(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada, ada_id, _ = await ada_and_grace(app, mailer)
    await ada.aclose()

    result = run_cli(["--email", ADA, "--yes"])

    assert result.returncode == 0, result.stderr
    assert await accounts(clean_database, ADA) == 0
    assert ada_id in result.stdout
    assert ADA not in result.stdout + result.stderr


async def test_a_fresh_interpreter_without_yes_refuses(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada, _, _ = await ada_and_grace(app, mailer)
    await ada.aclose()

    result = run_cli(["--email", ADA])

    assert result.returncode == 2
    assert "--yes" in result.stderr
    assert ADA not in result.stdout + result.stderr
    assert await accounts(clean_database, ADA) == 1
