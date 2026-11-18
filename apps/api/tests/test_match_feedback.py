"""Match feedback: written only with consent, idempotent, and purged when it lapses."""

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.discovery.service import DiscoveryService
from tests.conftest import MemoryMailer
from tests.helpers import PASSWORD, profile, scalar, student, user_id

pytestmark = pytest.mark.usefixtures("clean_database")

ADA = "ada@northeastern.edu"
GRACE = "grace@northeastern.edu"

FEEDBACK_URL = "/api/v1/discovery/feedback"


async def feedback_count(
    engine: AsyncEngine, viewer_id: str, candidate_id: str | None = None
) -> int:
    if candidate_id is None:
        return int(
            str(
                await scalar(
                    engine, "SELECT count(*) FROM match_feedback WHERE viewer_id = :v", v=viewer_id
                )
            )
        )
    return int(
        str(
            await scalar(
                engine,
                "SELECT count(*) FROM match_feedback WHERE viewer_id = :v AND candidate_id = :c",
                v=viewer_id,
                c=candidate_id,
            )
        )
    )


async def send_feedback(
    client: httpx.AsyncClient, candidate_id: str, action: str = "connected"
) -> httpx.Response:
    return await client.post(FEEDBACK_URL, json={"candidate_id": candidate_id, "action": action})


async def delete_account(client: httpx.AsyncClient, password: str = PASSWORD) -> httpx.Response:
    return await client.request("DELETE", "/api/v1/me", json={"password": password})


async def test_feedback_without_consent_is_accepted_but_not_recorded(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student(app, mailer, ADA)
    grace = await student(app, mailer, GRACE)
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    # The default PROFILE has feedback_consent False.
    await ada.put("/api/v1/me/profile", json=profile())
    await grace.put("/api/v1/me/profile", json=profile(display_name="Grace Hopper"))

    response = await send_feedback(ada, grace_id)
    assert response.status_code == 204
    assert await feedback_count(clean_database, ada_id, grace_id) == 0


async def test_feedback_with_consent_is_recorded_once(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student(app, mailer, ADA)
    grace = await student(app, mailer, GRACE)
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    await ada.put("/api/v1/me/profile", json=profile(feedback_consent=True))
    await grace.put("/api/v1/me/profile", json=profile(display_name="Grace Hopper"))

    first = await send_feedback(ada, grace_id)
    second = await send_feedback(ada, grace_id)
    assert first.status_code == 204
    assert second.status_code == 204
    assert await feedback_count(clean_database, ada_id, grace_id) == 1


async def test_feedback_for_a_hidden_candidate_is_not_recorded(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student(app, mailer, ADA)
    grace = await student(app, mailer, GRACE)
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    await ada.put("/api/v1/me/profile", json=profile(feedback_consent=True))
    await grace.put(
        "/api/v1/me/profile", json=profile(display_name="Grace Hopper", discoverable=False)
    )

    response = await send_feedback(ada, grace_id)
    assert response.status_code == 204
    assert await feedback_count(clean_database, ada_id, grace_id) == 0


async def test_feedback_for_a_blocked_candidate_is_not_recorded(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student(app, mailer, ADA)
    grace = await student(app, mailer, GRACE)
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    await ada.put("/api/v1/me/profile", json=profile(feedback_consent=True))
    await grace.put("/api/v1/me/profile", json=profile(display_name="Grace Hopper"))
    await ada.post("/api/v1/blocks", json={"user_id": grace_id})

    response = await send_feedback(ada, grace_id)
    assert response.status_code == 204
    assert await feedback_count(clean_database, ada_id, grace_id) == 0


async def test_feedback_about_yourself_is_not_recorded(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student(app, mailer, ADA)
    ada_id = await user_id(ada)
    await ada.put("/api/v1/me/profile", json=profile(feedback_consent=True))

    response = await send_feedback(ada, ada_id)
    assert response.status_code == 204
    assert await feedback_count(clean_database, ada_id, ada_id) == 0


async def test_withdrawing_consent_purges_feedback(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student(app, mailer, ADA)
    grace = await student(app, mailer, GRACE)
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    await ada.put("/api/v1/me/profile", json=profile(feedback_consent=True))
    await grace.put("/api/v1/me/profile", json=profile(display_name="Grace Hopper"))
    await send_feedback(ada, grace_id)
    assert await feedback_count(clean_database, ada_id) == 1

    await ada.put("/api/v1/me/profile", json=profile(feedback_consent=False))
    assert await feedback_count(clean_database, ada_id) == 0


async def test_opting_out_of_discovery_purges_feedback(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student(app, mailer, ADA)
    grace = await student(app, mailer, GRACE)
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    await ada.put("/api/v1/me/profile", json=profile(feedback_consent=True, discoverable=True))
    await grace.put("/api/v1/me/profile", json=profile(display_name="Grace Hopper"))
    await send_feedback(ada, grace_id)
    assert await feedback_count(clean_database, ada_id) == 1

    await ada.put("/api/v1/me/profile", json=profile(feedback_consent=True, discoverable=False))
    assert await feedback_count(clean_database, ada_id) == 0


async def test_deleting_the_viewer_account_removes_their_feedback(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student(app, mailer, ADA)
    grace = await student(app, mailer, GRACE)
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    await ada.put("/api/v1/me/profile", json=profile(feedback_consent=True))
    await grace.put("/api/v1/me/profile", json=profile(display_name="Grace Hopper"))
    await send_feedback(ada, grace_id)
    assert await feedback_count(clean_database, ada_id) == 1

    assert (await delete_account(ada)).status_code == 204
    assert await feedback_count(clean_database, ada_id) == 0


async def test_deleting_the_candidate_account_removes_feedback_about_them(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student(app, mailer, ADA)
    grace = await student(app, mailer, GRACE)
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    await ada.put("/api/v1/me/profile", json=profile(feedback_consent=True))
    await grace.put("/api/v1/me/profile", json=profile(display_name="Grace Hopper"))
    await send_feedback(ada, grace_id)
    assert await feedback_count(clean_database, ada_id, grace_id) == 1

    assert (await delete_account(grace)).status_code == 204
    assert await feedback_count(clean_database, ada_id, grace_id) == 0


async def test_a_bad_action_is_rejected(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student(app, mailer, ADA)
    grace = await student(app, mailer, GRACE)
    grace_id = await user_id(grace)
    await ada.put("/api/v1/me/profile", json=profile(feedback_consent=True))

    response = await send_feedback(ada, grace_id, action="liked")
    assert response.status_code == 422


async def test_a_consent_withdrawal_in_flight_blocks_feedback_until_it_can_see_it(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    """A profile save withdrawing consent holds a lock on the viewer's profile row from its
    upsert until it commits. A concurrent feedback write must wait for that commit and then
    re-read the withdrawn consent, rather than racing ahead on a stale read and writing a row
    the save's purge can no longer see."""
    ada = await student(app, mailer, ADA)
    grace = await student(app, mailer, GRACE)
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    await ada.put("/api/v1/me/profile", json=profile(feedback_consent=True))
    await grace.put("/api/v1/me/profile", json=profile(display_name="Grace Hopper"))

    # Simulate a profile save already mid-flight: it has withdrawn consent and run its purge,
    # but hasn't committed yet, so its lock on the profile row is still held. The try/finally
    # guarantees this never leaves an open transaction behind (it would wedge clean_database's
    # own TRUNCATE teardown against a lock no one will ever release) even if an assertion fails.
    connection = await clean_database.connect()
    try:
        transaction = await connection.begin()
        await connection.execute(
            text("UPDATE profiles SET feedback_consent = false WHERE user_id = :v"), {"v": ada_id}
        )
        await connection.execute(
            text("DELETE FROM match_feedback WHERE viewer_id = :v"), {"v": ada_id}
        )

        task = asyncio.create_task(send_feedback(ada, grace_id))
        try:
            await asyncio.sleep(0.3)
            assert not task.done()
        finally:
            await transaction.commit()

        response = await task
        assert response.status_code == 204
        assert await feedback_count(clean_database, ada_id, grace_id) == 0
    finally:
        await connection.close()


async def test_feedback_for_a_candidate_deleted_mid_request_is_not_a_500(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_may_record` and the insert aren't atomic: the candidate (or the viewer) could be
    deleted in between, turning the insert into a ForeignKeyViolation. That must still answer
    204 with nothing stored, not a 500."""
    ada = await student(app, mailer, ADA)
    await ada.put("/api/v1/me/profile", json=profile(feedback_consent=True))
    ghost_id = str(uuid.uuid4())

    async def fake_may_record(self: object, viewer_id: object, candidate_id: object) -> bool:
        return True

    monkeypatch.setattr(DiscoveryService, "_may_record", fake_may_record)

    response = await send_feedback(ada, ghost_id)
    assert response.status_code == 204
    assert (
        await scalar(
            clean_database,
            "SELECT count(*) FROM match_feedback WHERE candidate_id = :c",
            c=ghost_id,
        )
    ) == 0
