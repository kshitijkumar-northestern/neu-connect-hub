import asyncio
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.security import CSRF_COOKIE, SESSION_COOKIE
from app.db.base import Base
from app.modules.auth.service import ACCOUNT_DELETION_ATTEMPTS
from tests.conftest import WEB_ORIGIN, MemoryMailer
from tests.helpers import (
    PASSWORD,
    PROFILE,
    connect,
    execute,
    login,
    make_project_chat,
    scalar,
    student,
    user_id,
)

pytestmark = pytest.mark.usefixtures("clean_database")

ADA = "ada@northeastern.edu"
GRACE = "grace@northeastern.edu"


def references_to_users() -> list[tuple[str, str]]:
    """Every (table, column) whose foreign key points at an account, directly or via profiles."""
    account_keys = {"users.id", "profiles.user_id"}
    return sorted(
        (table.name, fk.parent.name)
        for table in Base.metadata.sorted_tables
        for fk in table.foreign_keys
        if fk.target_fullname in account_keys
    )


async def rows_referencing(engine: AsyncEngine, user: str) -> dict[str, int]:
    counts = {}
    for table, column in references_to_users():
        sql = f'SELECT count(*) FROM "{table}" WHERE "{column}" = :id'  # noqa: S608 - names from metadata
        counts[f"{table}.{column}"] = int(str(await scalar(engine, sql, id=user)))
    return counts


async def delete_account(client: httpx.AsyncClient, password: str = PASSWORD) -> httpx.Response:
    return await client.request("DELETE", "/api/v1/me", json={"password": password})


async def test_the_reference_scan_covers_every_account_table() -> None:
    tables = {table for table, _ in references_to_users()}
    assert {
        "sessions",
        "email_verifications",
        "password_resets",
        "profiles",
        "profile_skills",
        "profile_interests",
        "availability_slots",
        "blocks",
        "reports",
        "connections",
        "conversation_members",
        "projects",
        "project_members",
        "applications",
    } <= tables


async def test_deleting_an_account_leaves_no_rows_tied_to_it(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student(app, mailer, ADA)
    grace = await student(app, mailer, GRACE)
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    second_browser = await student(app, mailer, ADA)  # a second session for the same account

    await ada.put("/api/v1/me/profile", json=PROFILE)
    await grace.put("/api/v1/me/profile", json=PROFILE)
    await ada.post("/api/v1/blocks", json={"user_id": grace_id})
    await grace.post("/api/v1/blocks", json={"user_id": ada_id})
    await ada.post("/api/v1/reports", json={"user_id": grace_id, "reason": "spam"})
    await grace.post("/api/v1/reports", json={"user_id": ada_id, "reason": "harassment"})
    await ada.post("/api/v1/auth/password-reset/request", json={"email": ADA})
    before = await rows_referencing(clean_database, ada_id)
    assert all(
        before[key] > 0 for key in ("profiles.user_id", "blocks.blocked_id", "sessions.user_id")
    )

    response = await delete_account(ada)
    assert response.status_code == 204
    cleared = {cookie.split("=", 1)[0] for cookie in response.headers.get_list("set-cookie")}
    assert {SESSION_COOKIE, CSRF_COOKIE} <= cleared

    after = await rows_referencing(clean_database, ada_id)
    assert after == dict.fromkeys(after, 0)
    assert await scalar(clean_database, "SELECT count(*) FROM users WHERE id = :id", id=ada_id) == 0

    # Grace keeps her account and profile. Ada's report about her stays for moderators,
    # with no reporter; Grace's report about Ada goes with Ada's account.
    assert (await grace.get("/api/v1/me/profile")).status_code == 200
    assert await scalar(clean_database, "SELECT count(*) FROM reports") == 1
    assert (
        await scalar(clean_database, "SELECT count(*) FROM reports WHERE reporter_id IS NULL") == 1
    )

    for client in (ada, second_browser):
        assert (await client.get("/api/v1/me")).status_code == 401
    assert (await login(grace, ADA)).status_code == 401
    for client in (ada, grace, second_browser):
        await client.aclose()


async def test_a_wrong_password_deletes_nothing(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student(app, mailer, ADA)
    try:
        response = await delete_account(ada, "not my password at all")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "password_incorrect"
        assert (await delete_account(ada, "")).status_code == 422
        assert (await ada.request("DELETE", "/api/v1/me")).status_code == 422
        assert (await ada.get("/api/v1/me")).status_code == 200
        assert await scalar(clean_database, "SELECT count(*) FROM users") == 1
    finally:
        await ada.aclose()


async def test_password_guesses_for_deletion_are_rate_limited(
    app: object, mailer: MemoryMailer
) -> None:
    ada = await student(app, mailer, ADA)
    try:
        for _ in range(ACCOUNT_DELETION_ATTEMPTS.max_events):
            assert (await delete_account(ada, "wrong password guess")).status_code == 403
        limited = await delete_account(ada)
        assert limited.status_code == 429
        assert (await ada.get("/api/v1/me")).status_code == 200
    finally:
        await ada.aclose()


async def test_deletion_needs_the_csrf_token(app: object, mailer: MemoryMailer) -> None:
    ada = await student(app, mailer, ADA)
    try:
        del ada.headers["X-CSRF-Token"]
        assert (await delete_account(ada)).status_code == 403
        assert (await ada.get("/api/v1/me")).status_code == 200
    finally:
        await ada.aclose()


async def test_unverified_accounts_can_be_deleted(app: object, mailer: MemoryMailer) -> None:
    ada = await student(app, mailer, ADA, verified=False)
    try:
        assert (await delete_account(ada)).status_code == 204
    finally:
        await ada.aclose()


async def test_the_address_can_register_again_after_deletion(
    app: object, mailer: MemoryMailer
) -> None:
    ada = await student(app, mailer, ADA)
    old_id = await user_id(ada)
    await delete_account(ada)
    await ada.aclose()

    again = await student(app, mailer, ADA)
    try:
        assert await user_id(again) != old_id
        assert (await again.get("/api/v1/me/profile")).status_code == 404
    finally:
        await again.aclose()


async def test_deleting_an_account_disconnects_its_sockets(
    live_server: str, mailer: MemoryMailer
) -> None:
    http = await student(live_server, mailer, ADA)
    socket = await connect(live_server, WEB_ORIGIN, http.cookies[SESSION_COOKIE])
    disconnected = asyncio.Event()
    socket.on("disconnect", lambda *_: disconnected.set())
    try:
        assert (await delete_account(http)).status_code == 204
        async with asyncio.timeout(5):
            await disconnected.wait()
    finally:
        await http.aclose()


async def insert_message(
    engine: AsyncEngine, conversation_id: str, author_id: str, body: str
) -> str:
    message_id = str(uuid.uuid4())
    await execute(
        engine,
        "INSERT INTO messages (id, conversation_id, author_id, client_msg_id, body) "
        "VALUES (:id, :cid, :aid, :cm, :body)",
        id=message_id,
        cid=conversation_id,
        aid=author_id,
        cm=str(uuid.uuid4()),
        body=body,
    )
    return message_id


async def test_deleting_an_owner_deletes_their_project_and_team_chat_with_messages(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student(app, mailer, ADA)
    grace = await student(app, mailer, GRACE)
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    project_id, team_chat = await make_project_chat(clean_database, ada_id, [grace_id])
    await insert_message(clean_database, team_chat, ada_id, "owner's message")
    await insert_message(clean_database, team_chat, grace_id, "member's message")

    response = await delete_account(ada)
    assert response.status_code == 204

    assert (
        await scalar(clean_database, "SELECT count(*) FROM projects WHERE id = :id", id=project_id)
        == 0
    )
    assert (
        await scalar(
            clean_database, "SELECT count(*) FROM conversations WHERE id = :id", id=team_chat
        )
        == 0
    )
    assert (
        await scalar(
            clean_database,
            "SELECT count(*) FROM messages WHERE conversation_id = :id",
            id=team_chat,
        )
        == 0
    )
    # Grace's own account and profile are untouched by her former owner's deletion.
    assert (
        await scalar(clean_database, "SELECT count(*) FROM users WHERE id = :id", id=grace_id) == 1
    )


async def test_deleting_a_member_keeps_the_project_and_the_others_messages(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student(app, mailer, ADA)
    grace = await student(app, mailer, GRACE)
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    project_id, team_chat = await make_project_chat(clean_database, ada_id, [grace_id])
    await insert_message(clean_database, team_chat, ada_id, "owner's message")
    grace_message_id = await insert_message(clean_database, team_chat, grace_id, "member's message")

    response = await delete_account(grace)
    assert response.status_code == 204

    assert (
        await scalar(clean_database, "SELECT count(*) FROM projects WHERE id = :id", id=project_id)
        == 1
    )
    assert (
        await scalar(
            clean_database, "SELECT count(*) FROM conversations WHERE id = :id", id=team_chat
        )
        == 1
    )
    assert (
        await scalar(
            clean_database,
            "SELECT count(*) FROM project_members WHERE project_id = :pid AND user_id = :uid",
            pid=project_id,
            uid=grace_id,
        )
        == 0
    )
    assert (
        await scalar(
            clean_database, "SELECT body FROM messages WHERE id = :id", id=grace_message_id
        )
        is None
    )
    assert (
        await scalar(
            clean_database,
            "SELECT (removed_at IS NOT NULL) FROM messages WHERE id = :id",
            id=grace_message_id,
        )
        is True
    )
    assert (
        await scalar(
            clean_database,
            "SELECT body FROM messages WHERE conversation_id = :id AND author_id = :aid",
            id=team_chat,
            aid=ada_id,
        )
        == "owner's message"
    )
