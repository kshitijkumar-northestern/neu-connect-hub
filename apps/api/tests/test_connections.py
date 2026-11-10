import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.rate_limit import Limit
from app.modules.connections import service as connection_service
from tests.conftest import MemoryMailer
from tests.helpers import PROFILE, execute, new_client, scalar, student, user_id

pytestmark = pytest.mark.usefixtures("clean_database")


async def with_profile(
    app: object, mailer: MemoryMailer, email: str, **changes: Any
) -> httpx.AsyncClient:
    client = await student(app, mailer, email)
    name = email.split("@")[0].title()
    response = await client.put(
        "/api/v1/me/profile", json={**PROFILE, "display_name": name, **changes}
    )
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
async def ada(app: object, mailer: MemoryMailer) -> AsyncIterator[httpx.AsyncClient]:
    client = await with_profile(app, mailer, "ada@northeastern.edu")
    yield client
    await client.aclose()


@pytest.fixture
async def grace(app: object, mailer: MemoryMailer) -> AsyncIterator[httpx.AsyncClient]:
    client = await with_profile(app, mailer, "grace@northeastern.edu")
    yield client
    await client.aclose()


@pytest.fixture
async def eve(app: object, mailer: MemoryMailer) -> AsyncIterator[httpx.AsyncClient]:
    client = await with_profile(app, mailer, "eve@northeastern.edu")
    yield client
    await client.aclose()


async def request(client: httpx.AsyncClient, target: str) -> httpx.Response:
    return await client.post("/api/v1/connections", json={"user_id": target})


async def act(client: httpx.AsyncClient, connection_id: str, action: str) -> httpx.Response:
    return await client.post(f"/api/v1/connections/{connection_id}/{action}")


async def listed(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await client.get("/api/v1/connections")
    assert response.status_code == 200, response.text
    body: list[dict[str, Any]] = response.json()
    return body


async def count(engine: AsyncEngine, table: str) -> int:
    return int(str(await scalar(engine, f'SELECT count(*) FROM "{table}"')))  # noqa: S608


# --- sending --------------------------------------------------------------------------------


async def test_a_request_shows_up_for_both_students(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    response = await request(ada, grace_id)
    assert response.status_code == 201, response.text
    sent = response.json()
    assert sent["state"] == "pending"
    assert sent["direction"] == "outgoing"
    assert (sent["user_id"], sent["display_name"]) == (grace_id, "Grace")
    assert sent["conversation_id"] is None

    [incoming] = await listed(grace)
    assert incoming["id"] == sent["id"]
    assert (incoming["direction"], incoming["user_id"], incoming["display_name"]) == (
        "incoming",
        ada_id,
        "Ada",
    )
    assert "grace@northeastern.edu" not in str(await listed(ada))


async def test_students_cannot_connect_with_themselves(ada: httpx.AsyncClient) -> None:
    response = await request(ada, await user_id(ada))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "cannot_target_self"


async def test_hidden_blocked_and_unknown_students_cannot_be_asked(
    app: object, mailer: MemoryMailer, ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    hidden = await with_profile(app, mailer, "hidden@northeastern.edu", discoverable=False)
    no_profile = await student(app, mailer, "noprofile@northeastern.edu")
    await grace.post("/api/v1/blocks", json={"user_id": await user_id(ada)})

    missing = await request(ada, str(uuid.uuid4()))
    assert missing.status_code == 404
    for target in (await user_id(hidden), await user_id(no_profile), await user_id(grace)):
        response = await request(ada, target)
        assert response.status_code == 404, target
        assert response.json()["error"]["code"] == missing.json()["error"]["code"]
    assert await listed(ada) == []
    for client in (hidden, no_profile):
        await client.aclose()


async def test_repeated_and_crossed_requests_leave_one_pending_row(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    assert (await request(ada, grace_id)).status_code == 201

    again = await request(ada, grace_id)
    crossed = await request(grace, ada_id)
    for response in (again, crossed):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "connection_exists"
    assert await count(clean_database, "connections") == 1


async def test_concurrent_crossed_requests_create_exactly_one(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    responses = await asyncio.gather(
        *[request(ada, grace_id) for _ in range(3)], *[request(grace, ada_id) for _ in range(3)]
    )
    assert sorted(r.status_code for r in responses) == [201] + [409] * 5
    assert await count(clean_database, "connections") == 1


async def test_requests_are_rate_limited(
    app: object,
    mailer: MemoryMailer,
    ada: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        connection_service, "REQUESTS_PER_USER", Limit(max_events=2, window_seconds=3600)
    )
    targets = []
    for name in ("one", "two", "three"):
        other = await with_profile(app, mailer, f"{name}@northeastern.edu")
        targets.append(await user_id(other))
        await other.aclose()

    assert [(await request(ada, target)).status_code for target in targets] == [201, 201, 429]


async def test_connection_routes_need_a_verified_student(
    app: object, mailer: MemoryMailer, grace: httpx.AsyncClient
) -> None:
    unverified = await student(app, mailer, "new@northeastern.edu", verified=False)
    async with new_client(app) as anonymous:
        assert (await anonymous.get("/api/v1/connections")).status_code == 401
    response = await request(unverified, await user_id(grace))
    assert response.status_code == 403
    assert (await unverified.get("/api/v1/connections")).status_code == 403
    await unverified.aclose()


# --- answering ------------------------------------------------------------------------------


async def test_only_the_recipient_can_accept_or_decline(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, eve: httpx.AsyncClient
) -> None:
    sent = (await request(ada, await user_id(grace))).json()

    for client in (ada, eve):  # the sender and a third party
        for action in ("accept", "decline"):
            response = await act(client, sent["id"], action)
            assert response.status_code == 404, (action, response.text)
    assert (await act(eve, sent["id"], "withdraw")).status_code == 404
    assert (await act(grace, sent["id"], "withdraw")).status_code == 404
    assert [c["state"] for c in await listed(grace)] == ["pending"]


async def test_accepting_creates_one_direct_conversation(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    sent = (await request(ada, grace_id)).json()

    accepted = await act(grace, sent["id"], "accept")
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert (body["state"], body["direction"], body["user_id"]) == ("accepted", "incoming", ada_id)
    assert body["conversation_id"] is not None and body["responded_at"] is not None

    members = await scalar(
        clean_database,
        "SELECT string_agg(user_id::text || ':' || active::text, ',' ORDER BY user_id) "
        "FROM conversation_members m JOIN conversations c ON c.id = m.conversation_id "
        "WHERE c.id = :id AND c.kind = 'direct'",
        id=body["conversation_id"],
    )
    assert members == ",".join(f"{uid}:true" for uid in sorted([ada_id, grace_id]))
    [mine] = await listed(ada)
    assert (mine["state"], mine["conversation_id"]) == ("accepted", body["conversation_id"])


async def test_concurrent_and_repeated_accepts_agree(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    sent = (await request(ada, await user_id(grace))).json()

    responses = await asyncio.gather(*[act(grace, sent["id"], "accept") for _ in range(5)])
    assert [r.status_code for r in responses] == [200] * 5
    retry = await act(grace, sent["id"], "accept")
    conversations = {r.json()["conversation_id"] for r in [*responses, retry]}
    assert len(conversations) == 1
    assert await count(clean_database, "conversations") == 1
    assert await count(clean_database, "conversation_members") == 2


async def test_a_declined_request_stays_closed_and_can_be_asked_again(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    grace_id = await user_id(grace)
    sent = (await request(ada, grace_id)).json()

    assert (await act(grace, sent["id"], "decline")).status_code == 204
    assert (await act(grace, sent["id"], "decline")).status_code == 204  # idempotent
    closed = await act(grace, sent["id"], "accept")
    assert closed.status_code == 409
    assert closed.json()["error"]["code"] == "connection_closed"
    assert await listed(ada) == [] and await listed(grace) == []

    assert (await request(ada, grace_id)).status_code == 201


async def test_the_sender_can_withdraw_a_pending_request(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    sent = (await request(ada, await user_id(grace))).json()

    assert (await act(ada, sent["id"], "withdraw")).status_code == 204
    assert (await act(ada, sent["id"], "withdraw")).status_code == 204
    assert (await act(grace, sent["id"], "accept")).status_code == 409
    assert await listed(grace) == []


async def test_an_accepted_connection_cannot_be_withdrawn_or_declined(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    sent = (await request(ada, await user_id(grace))).json()
    await act(grace, sent["id"], "accept")
    assert (await act(ada, sent["id"], "withdraw")).status_code == 409
    assert (await act(grace, sent["id"], "decline")).status_code == 409
    assert [c["state"] for c in await listed(ada)] == ["accepted"]


async def test_unknown_connection_ids_are_not_found(ada: httpx.AsyncClient) -> None:
    for action in ("accept", "decline", "withdraw"):
        assert (await act(ada, str(uuid.uuid4()), action)).status_code == 404


# --- connections, visibility and blocks -----------------------------------------------------


async def test_connected_students_still_see_each_other_when_hidden(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, eve: httpx.AsyncClient
) -> None:
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    sent = (await request(ada, grace_id)).json()
    await act(grace, sent["id"], "accept")
    await grace.put("/api/v1/me/profile", json={**PROFILE, "discoverable": False})

    assert (await ada.get(f"/api/v1/profiles/{grace_id}")).status_code == 200
    assert (await eve.get(f"/api/v1/profiles/{grace_id}")).status_code == 404
    assert (await grace.get(f"/api/v1/profiles/{ada_id}")).status_code == 200


async def test_a_pending_request_alone_does_not_reveal_a_hidden_profile(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    ada_id = await user_id(ada)
    await request(ada, await user_id(grace))
    await ada.put("/api/v1/me/profile", json={**PROFILE, "discoverable": False})
    assert (await grace.get(f"/api/v1/profiles/{ada_id}")).status_code == 404


async def test_a_block_ends_the_connection_and_deactivates_the_conversation(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    sent = (await request(ada, grace_id)).json()
    accepted = (await act(grace, sent["id"], "accept")).json()

    assert (await ada.post("/api/v1/blocks", json={"user_id": grace_id})).status_code == 204
    assert await listed(ada) == [] and await listed(grace) == []
    assert (await grace.get(f"/api/v1/profiles/{ada_id}")).status_code == 404
    active = await scalar(
        clean_database,
        "SELECT count(*) FROM conversation_members WHERE conversation_id = :id AND active",
        id=accepted["conversation_id"],
    )
    assert active == 0

    # Unblocking doesn't reconnect them; they would have to ask again.
    await ada.delete(f"/api/v1/blocks/{grace_id}")
    assert await listed(ada) == []
    assert (await request(grace, ada_id)).status_code == 201


async def test_a_block_ends_a_pending_request_so_it_cannot_be_accepted(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    sent = (await request(ada, await user_id(grace))).json()
    await ada.post("/api/v1/blocks", json={"user_id": await user_id(grace)})
    response = await act(grace, sent["id"], "accept")
    assert response.status_code == 409
    assert await listed(grace) == []


async def test_a_racing_block_never_lets_a_request_be_accepted(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    sent = (await request(ada, await user_id(grace))).json()
    # A block row that appeared without ending the request (as if it raced the insert).
    await execute(
        clean_database,
        "INSERT INTO blocks (blocker_id, blocked_id) VALUES (:a, :b)",
        a=await user_id(ada),
        b=await user_id(grace),
    )
    assert (await act(grace, sent["id"], "accept")).status_code == 404
    assert await count(clean_database, "conversations") == 0
