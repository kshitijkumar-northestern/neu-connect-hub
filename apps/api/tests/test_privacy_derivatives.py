"""One student with every kind of row the app keeps about them, and what each privacy control
leaves behind: deleting the account erases all of it, hiding the profile removes exactly the
derived data, and withdrawing feedback consent removes exactly the feedback."""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import MemoryMailer
from tests.helpers import (
    execute,
    make_admin,
    profile,
    scalar,
    student_with_profile,
    user_id,
)
from tests.test_account_deletion import (
    delete_account,
    insert_message,
    references_to_users,
    rows_referencing,
)
from tests.test_embedding_lifecycle import mark_fresh

pytestmark = [
    pytest.mark.usefixtures("clean_database"),
    pytest.mark.filterwarnings("error::sqlalchemy.exc.SAWarning"),
]

ADA = "ada@northeastern.edu"
GRACE = "grace@northeastern.edu"
EVE = "eve@northeastern.edu"
HAL = "hal@northeastern.edu"

# What hiding a profile removes: everything derived from it being discoverable.
OPT_OUT_PURGES = frozenset(
    {"embeddings.user_id", "match_feedback.viewer_id", "profile_share_tokens.user_id"}
)
PROJECT = {
    "title": "Music Recommendation Engine",
    "summary": "Building a music recommender for study playlists.",
    "capacity": 5,
    "roles": [{"title": "ML Engineer", "open_slots": 1, "skills": ["machine-learning"]}],
}


@dataclass
class World:
    ada: httpx.AsyncClient
    ada_id: str
    others: list[httpx.AsyncClient]
    # Audit rows naming Ada: one she wrote as an admin, one written about her.
    audit_ids: list[str]

    async def close(self) -> None:
        for client in (self.ada, *self.others):
            await client.aclose()


def ok(response: httpx.Response, status: int = 200) -> httpx.Response:
    assert response.status_code == status, response.text
    return response


async def build_world(app: object, mailer: MemoryMailer, engine: AsyncEngine) -> World:
    """Ada, with a row in every table that can point at an account. Everything a student can
    do goes through the API; only what has no HTTP route (sending a message, joining a call,
    the encoder's output and the admin side) is written straight to the database."""
    ada = await student_with_profile(app, mailer, ADA, display_name="Ada", feedback_consent=True)
    grace = await student_with_profile(app, mailer, GRACE, feedback_consent=True)
    eve = await student_with_profile(app, mailer, EVE)
    hal = await student_with_profile(app, mailer, HAL)
    ada_id, grace_id, eve_id, hal_id = [await user_id(c) for c in (ada, grace, eve, hal)]

    # The encoder's output, and feedback both ways.
    await mark_fresh(engine, ada_id)
    feedback = "/api/v1/discovery/feedback"
    ok(await ada.post(feedback, json={"candidate_id": grace_id, "action": "connected"}), 204)
    ok(await grace.post(feedback, json={"candidate_id": ada_id, "action": "connected"}), 204)

    # A QR share code and a password reset link.
    ok(await ada.post("/api/v1/me/connection-code/rotate"), 201)
    ok(await ada.post("/api/v1/auth/password-reset/request", json={"email": ADA}), 202)

    # A connection she sent (with its conversation, messages and a call) and one she received.
    sent = (ok(await ada.post("/api/v1/connections", json={"user_id": grace_id}), 201)).json()
    accepted = (ok(await grace.post(f"/api/v1/connections/{sent['id']}/accept"))).json()
    conversation_id = accepted["conversation_id"]
    await insert_message(engine, conversation_id, ada_id, "Ada's message")
    await insert_message(engine, conversation_id, grace_id, "Grace's message")
    room = ok(await ada.post("/api/v1/rooms", json={"conversation_id": conversation_id}), 201)
    await execute(
        engine,
        "INSERT INTO room_members (id, room_id, user_id) VALUES (:id, :room_id, :user_id)",
        id=str(uuid.uuid4()),
        room_id=room.json()["id"],
        user_id=ada_id,
    )
    ok(await hal.post("/api/v1/connections", json={"user_id": ada_id}), 201)

    # A project she owns, and an application to someone else's.
    ok(await ada.post("/api/v1/projects", json=PROJECT), 201)
    hals = (ok(await hal.post("/api/v1/projects", json=PROJECT), 201)).json()
    ok(await ada.post(f"/api/v1/projects/{hals['id']}/apply", json={}), 201)

    # Blocks and reports both ways, and a report about someone else for her to resolve.
    for blocker, blocked in ((ada, eve_id), (eve, ada_id)):
        ok(await blocker.post("/api/v1/blocks", json={"user_id": blocked}), 204)
    for reporter, target in ((ada, eve_id), (eve, ada_id), (grace, hal_id)):
        body = {"user_id": target, "reason": "spam"}
        ok(await reporter.post("/api/v1/reports", json=body), 202)

    # The admin side: Ada is promoted, enrols in MFA and dismisses Grace's report about Hal.
    await make_admin(engine, ada_id)
    await execute(
        engine,
        "INSERT INTO admin_mfa (user_id, nonce, confirmed_at, last_used_step)"
        " VALUES (:id, :nonce, now(), 1)",
        id=ada_id,
        nonce=b"0123456789abcdef",
    )
    await execute(
        engine,
        "INSERT INTO admin_recovery_codes (id, user_id, code_hash) VALUES (:id, :uid, :hash)",
        id=str(uuid.uuid4()),
        uid=ada_id,
        hash="c" * 64,
    )
    report_id = str(
        await scalar(engine, "SELECT id FROM reports WHERE reporter_id = :id", id=grace_id)
    )
    await execute(
        engine,
        "UPDATE reports SET status = 'dismissed', resolved_by = :ada, resolved_at = now()"
        " WHERE id = :id",
        ada=ada_id,
        id=report_id,
    )
    granted, dismissed = str(uuid.uuid4()), str(uuid.uuid4())
    await execute(
        engine,
        "INSERT INTO admin_audit_log (id, admin_id, action, target_user_id, report_id) VALUES"
        " (:granted, NULL, 'admin_granted', :ada, NULL),"
        " (:dismissed, :ada, 'report_dismissed', :hal, :report)",
        granted=granted,
        dismissed=dismissed,
        ada=ada_id,
        hal=hal_id,
        report=report_id,
    )
    return World(ada=ada, ada_id=ada_id, others=[grace, eve, hal], audit_ids=[granted, dismissed])


@pytest.fixture
async def world(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> AsyncIterator[World]:
    built = await build_world(app, mailer, clean_database)
    yield built
    await built.close()


async def test_the_student_has_a_row_in_every_table_that_can_point_at_an_account(
    world: World, clean_database: AsyncEngine
) -> None:
    """A new table with a foreign key to accounts fails here until it gets a row above, so the
    deletion and opt-out checks below always cover it too."""
    counts = await rows_referencing(clean_database, world.ada_id)
    assert set(counts) == {f"{table}.{column}" for table, column in references_to_users()}
    assert [key for key, count in counts.items() if count == 0] == []


async def test_deleting_the_account_leaves_no_row_pointing_at_it(
    world: World, clean_database: AsyncEngine
) -> None:
    assert (await delete_account(world.ada)).status_code == 204

    after = await rows_referencing(clean_database, world.ada_id)
    assert after == dict.fromkeys(after, 0)
    assert (
        await scalar(clean_database, "SELECT count(*) FROM users WHERE id = :id", id=world.ada_id)
        == 0
    )
    # Her message to Grace stays as a tombstone in Grace's conversation, with no body.
    assert (
        await scalar(
            clean_database,
            "SELECT count(*) FROM messages WHERE author_id IS NULL AND body IS NULL"
            " AND removed_at IS NOT NULL",
        )
        == 1
    )


async def test_audit_rows_outlive_the_account_with_its_ids_nulled(
    world: World, clean_database: AsyncEngine
) -> None:
    granted, dismissed = world.audit_ids
    assert (await delete_account(world.ada)).status_code == 204

    rows = {
        str(row_id): (admin_id, target_id, report_id)
        for row_id, admin_id, target_id, report_id in (
            await _all(
                clean_database,
                "SELECT id, admin_id, target_user_id, report_id FROM admin_audit_log",
            )
        )
    }
    assert set(rows) == {granted, dismissed}
    assert rows[granted] == (None, None, None)
    admin_id, target_id, report_id = rows[dismissed]
    assert admin_id is None
    # What she did as an admin still names the student and report it was about.
    assert target_id is not None and report_id is not None


async def test_hiding_the_profile_removes_exactly_its_derived_data(
    world: World, clean_database: AsyncEngine
) -> None:
    before = await rows_referencing(clean_database, world.ada_id)

    hidden = profile(display_name="Ada", feedback_consent=True, discoverable=False)
    ok(await world.ada.put("/api/v1/me/profile", json=hidden))

    after = await rows_referencing(clean_database, world.ada_id)
    changed = {key for key in before if after[key] != before[key]}
    assert changed == OPT_OUT_PURGES
    assert all(after[key] == 0 for key in OPT_OUT_PURGES)
    # The profile itself stays, and so does feedback others gave about her: that belongs to
    # the viewer who consented to it, and goes when either account is deleted.
    assert after["profiles.user_id"] == 1
    assert after["match_feedback.candidate_id"] == before["match_feedback.candidate_id"] > 0


async def test_opting_back_in_does_not_bring_the_share_code_back(
    world: World, clean_database: AsyncEngine
) -> None:
    hidden = profile(display_name="Ada", feedback_consent=True, discoverable=False)
    ok(await world.ada.put("/api/v1/me/profile", json=hidden))
    ok(await world.ada.put("/api/v1/me/profile", json={**hidden, "discoverable": True}))

    code = (ok(await world.ada.get("/api/v1/me/connection-code"))).json()
    assert code["status"] == "off"
    assert code["url"] is None
    assert (await rows_referencing(clean_database, world.ada_id))[
        "profile_share_tokens.user_id"
    ] == 0


async def test_withdrawing_feedback_consent_removes_only_her_feedback(
    world: World, clean_database: AsyncEngine
) -> None:
    before = await rows_referencing(clean_database, world.ada_id)

    withdrawn = profile(display_name="Ada", feedback_consent=False, discoverable=True)
    ok(await world.ada.put("/api/v1/me/profile", json=withdrawn))

    after = await rows_referencing(clean_database, world.ada_id)
    changed = {key for key in before if after[key] != before[key]}
    assert changed == {"match_feedback.viewer_id"}
    assert after["match_feedback.viewer_id"] == 0


async def _all(engine: AsyncEngine, sql: str) -> list[tuple[object, ...]]:
    async with engine.connect() as connection:
        return [tuple(row) for row in (await connection.execute(text(sql))).all()]


async def test_a_deletion_is_logged_by_account_id_only(
    app: object, mailer: MemoryMailer, caplog: pytest.LogCaptureFixture
) -> None:
    """The one record a restore needs to re-apply deletions made after its backup was taken."""
    ada = await student_with_profile(app, mailer, ADA)
    try:
        ada_id = await user_id(ada)
        with caplog.at_level(logging.INFO, logger="app"):
            assert (await delete_account(ada)).status_code == 204
    finally:
        await ada.aclose()

    deletions = [record for record in caplog.records if record.name == "app.accounts"]
    assert [getattr(record, "user_id", None) for record in deletions] == [ada_id]
    for record in caplog.records:
        assert ADA not in record.getMessage()
        assert ADA not in str(vars(record))


# --- hiding and concurrent writers ------------------------------------------------------------


async def share_codes(engine: AsyncEngine, uid: str) -> int:
    sql = "SELECT count(*) FROM profile_share_tokens WHERE user_id = :id"
    return int(str(await scalar(engine, sql, id=uid)))


async def test_hiding_waits_for_a_rotation_in_flight_and_removes_its_new_code(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    """A save takes the account's users-row lock first, as rotating and turning a code off do,
    so the two run one after the other in the same lock order and can't deadlock. Here a
    rotation is mid-flight: the hide waits for it, then purges the code it just issued too."""
    ada = await student_with_profile(app, mailer, ADA, display_name="Ada")
    try:
        ada_id = await user_id(ada)
        ok(await ada.post("/api/v1/me/connection-code/rotate"), 201)
        hidden = profile(display_name="Ada", discoverable=False)

        connection = await clean_database.connect()
        try:
            transaction = await connection.begin()
            # ScanService.rotate's statements, held open before its commit.
            await connection.execute(
                text("SELECT id FROM users WHERE id = :id FOR UPDATE"), {"id": ada_id}
            )
            await connection.execute(
                text(
                    "UPDATE profile_share_tokens SET active = false, revoked_at = now()"
                    " WHERE user_id = :id AND active"
                ),
                {"id": ada_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO profile_share_tokens (id, user_id, token_hash, expires_at)"
                    " VALUES (:id, :uid, :hash, now() + interval '1 day')"
                ),
                {"id": str(uuid.uuid4()), "uid": ada_id, "hash": "d" * 64},
            )
            task = asyncio.create_task(ada.put("/api/v1/me/profile", json=hidden))
            try:
                await asyncio.sleep(0.3)
                assert not task.done()
            finally:
                await transaction.commit()
            async with asyncio.timeout(10):
                ok(await task)
        finally:
            await connection.close()

        assert await share_codes(clean_database, ada_id) == 0
    finally:
        await ada.aclose()


async def test_hiding_and_a_feedback_write_in_flight_never_deadlock(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    """Feedback locks the viewer's profile row (FOR SHARE), then its insert takes FOR KEY SHARE
    on both accounts' users rows. A save's users-row lock must not conflict with FOR KEY SHARE:
    if it did, a save waiting for the profile row and a feedback insert waiting for the users
    row would deadlock."""
    ada = await student_with_profile(app, mailer, ADA, display_name="Ada", feedback_consent=True)
    grace = await student_with_profile(app, mailer, GRACE)
    try:
        ada_id, grace_id = await user_id(ada), await user_id(grace)
        hidden = profile(display_name="Ada", feedback_consent=True, discoverable=False)

        connection = await clean_database.connect()
        try:
            transaction = await connection.begin()
            try:
                # DiscoveryService.feedback's statements, in its order, held open.
                await connection.execute(
                    text("SELECT user_id FROM profiles WHERE user_id = :id FOR SHARE"),
                    {"id": ada_id},
                )
                task = asyncio.create_task(ada.put("/api/v1/me/profile", json=hidden))
                await asyncio.sleep(0.3)
                assert not task.done()  # the save waits for the profile row
                async with asyncio.timeout(5):
                    await connection.execute(
                        text(
                            "INSERT INTO match_feedback (id, viewer_id, candidate_id, action)"
                            " VALUES (:id, :viewer, :candidate, 'connected')"
                        ),
                        {"id": str(uuid.uuid4()), "viewer": ada_id, "candidate": grace_id},
                    )
            except BaseException:
                await transaction.rollback()
                raise
            await transaction.commit()
            async with asyncio.timeout(10):
                ok(await task)
        finally:
            await connection.close()

        # The hide ran after the feedback committed, so its purge caught the new row.
        sql = "SELECT count(*) FROM match_feedback WHERE viewer_id = :id"
        assert await scalar(clean_database, sql, id=ada_id) == 0
    finally:
        await ada.aclose()
        await grace.aclose()


async def test_concurrent_hides_shows_rotations_and_disables_all_finish(
    app: object, mailer: MemoryMailer
) -> None:
    ada = await student_with_profile(app, mailer, ADA, display_name="Ada")
    try:
        hidden = profile(display_name="Ada", discoverable=False)
        shown = profile(display_name="Ada", discoverable=True)
        for _ in range(5):
            async with asyncio.timeout(15):
                responses = await asyncio.gather(
                    ada.put("/api/v1/me/profile", json=hidden),
                    ada.post("/api/v1/me/connection-code/rotate"),
                    ada.put("/api/v1/me/profile", json=shown),
                    ada.request("DELETE", "/api/v1/me/connection-code"),
                )
            assert [r.status_code for r in responses] == [200, 201, 200, 204]
    finally:
        await ada.aclose()
