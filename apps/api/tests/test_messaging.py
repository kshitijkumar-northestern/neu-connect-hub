import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.rate_limit import Limit
from app.modules.chat import service as chat_service
from app.realtime import chat as realtime_chat
from tests.conftest import MemoryMailer
from tests.helpers import (
    Student,
    connect_pair,
    eventually,
    execute,
    history,
    make_student,
    new_client,
    scalar,
)

pytestmark = pytest.mark.usefixtures("clean_database")


@pytest.fixture
async def trio(
    live_server: str, mailer: MemoryMailer
) -> AsyncIterator[tuple[Student, Student, Student, str]]:
    ada = await make_student(live_server, mailer, "ada")
    grace = await make_student(live_server, mailer, "grace")
    eve = await make_student(live_server, mailer, "eve")
    conversation = await connect_pair(ada, grace)
    yield ada, grace, eve, conversation
    for s in (ada, grace, eve):
        await s.close()


# --- sending and delivery -------------------------------------------------------------------


async def test_members_exchange_messages_in_real_time(
    trio: tuple[Student, Student, Student, str],
) -> None:
    ada, grace, eve, conversation = trio
    for s in (ada, grace, eve):
        await s.open_socket()

    ack = await ada.send(conversation, "  Hello Grace!  ")
    assert ack["ok"] is True, ack
    message = ack["message"]
    assert (message["body"], message["author_id"], message["removed"]) == (
        "Hello Grace!",
        ada.id,
        False,
    )

    await eventually(lambda: grace.received())
    assert grace.received() == [message]
    await eventually(lambda: ada.received())  # the sender's other tabs hear it too
    await asyncio.sleep(0.2)
    assert eve.received() == []

    page = (await history(grace, conversation)).json()
    assert [m["id"] for m in page["messages"]] == [message["id"]]


async def test_non_members_can_neither_read_nor_send(
    trio: tuple[Student, Student, Student, str],
) -> None:
    _, _, eve, conversation = trio
    await eve.open_socket()

    for target in (conversation, str(uuid.uuid4())):
        response = await history(eve, target)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
        ack = await eve.send(target, "let me in")
        assert ack == {
            "ok": False,
            "error": {"code": "not_found", "message": ack["error"]["message"]},
        }


async def test_repeated_client_msg_ids_store_one_message(
    trio: tuple[Student, Student, Student, str], clean_database: AsyncEngine
) -> None:
    ada, _, _, conversation = trio
    await ada.open_socket()
    client_msg_id = str(uuid.uuid4())

    acks = await asyncio.gather(*[ada.send(conversation, "once", client_msg_id) for _ in range(4)])
    assert all(ack["ok"] for ack in acks), acks
    assert len({ack["message"]["id"] for ack in acks}) == 1
    assert await scalar(clean_database, "SELECT count(*) FROM messages") == 1


async def test_a_reconnecting_client_catches_up_in_order(
    trio: tuple[Student, Student, Student, str],
) -> None:
    ada, grace, _, conversation = trio
    await ada.open_socket()
    grace_socket = await grace.open_socket()
    await ada.send(conversation, "before the drop")
    await eventually(lambda: grace.received())
    cursor = (await history(grace, conversation)).json()["after"]

    await grace_socket.disconnect()
    sent = [(await ada.send(conversation, f"missed {n}"))["message"]["id"] for n in range(3)]
    await grace.open_socket()

    caught_up = (await history(grace, conversation, after=cursor)).json()
    assert [m["id"] for m in caught_up["messages"]] == sent
    assert [m["body"] for m in caught_up["messages"]] == ["missed 0", "missed 1", "missed 2"]
    again = (await history(grace, conversation, after=caught_up["after"])).json()
    assert again["messages"] == [] and again["after"] == caught_up["after"]


async def test_paging_backwards_is_stable_while_new_messages_arrive(
    trio: tuple[Student, Student, Student, str],
) -> None:
    ada, grace, _, conversation = trio
    await ada.open_socket()
    first = [(await ada.send(conversation, f"m{n}"))["message"]["id"] for n in range(7)]

    page = (await history(grace, conversation, limit=3)).json()
    seen = [m["id"] for m in page["messages"]]
    assert seen == first[4:]
    await ada.send(conversation, "arrives while paging")
    while page["before"] is not None:
        page = (await history(grace, conversation, before=page["before"], limit=3)).json()
        seen = [m["id"] for m in page["messages"]] + seen
    assert seen == first


@pytest.mark.parametrize(
    "params",
    [{"before": "not-a-cursor"}, {"after": "%%%"}, {"limit": 0}, {"limit": 101}],
    ids=["bad before", "bad after", "zero limit", "huge limit"],
)
async def test_invalid_paging_parameters_are_rejected(
    trio: tuple[Student, Student, Student, str], params: dict[str, Any]
) -> None:
    ada, _, _, conversation = trio
    assert (await history(ada, conversation, **params)).status_code == 422


async def test_before_and_after_together_are_rejected(
    trio: tuple[Student, Student, Student, str],
) -> None:
    ada, _, _, conversation = trio
    await ada.open_socket()
    await ada.send(conversation, "one")
    cursor = (await history(ada, conversation)).json()["after"]
    assert (await history(ada, conversation, before=cursor, after=cursor)).status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"body": "   "},
        {"body": "x" * 4001},
        {"body": "hi‮"},
        {"client_msg_id": "not-a-uuid"},
        {"conversation_id": "nope"},
        {"extra": True},
    ],
    ids=["blank", "too long", "bidi override", "bad client id", "bad conversation", "extra key"],
)
async def test_invalid_messages_are_refused(
    trio: tuple[Student, Student, Student, str],
    clean_database: AsyncEngine,
    payload: dict[str, Any],
) -> None:
    ada, _, _, conversation = trio
    sock = await ada.open_socket()
    data = {"conversation_id": conversation, "client_msg_id": str(uuid.uuid4()), "body": "ok"}
    ack = await sock.call("chat.send", {**data, **payload}, timeout=5)
    assert ack["ok"] is False and ack["error"]["code"] == "validation_error", ack
    assert await scalar(clean_database, "SELECT count(*) FROM messages") == 0


async def test_sending_is_rate_limited(
    trio: tuple[Student, Student, Student, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(realtime_chat, "SENDS_PER_USER", Limit(max_events=2, window_seconds=60))
    ada, _, _, conversation = trio
    await ada.open_socket()
    acks = [await ada.send(conversation, f"m{n}") for n in range(3)]
    assert [a["ok"] for a in acks] == [True, True, False]
    assert acks[2]["error"]["code"] == "rate_limited"


# --- trust, sessions and privacy ------------------------------------------------------------


async def test_a_block_stops_delivery_and_history_at_once(
    trio: tuple[Student, Student, Student, str],
) -> None:
    ada, grace, _, conversation = trio
    await ada.open_socket()
    await grace.open_socket()
    await ada.send(conversation, "before")
    await eventually(lambda: len(grace.received()) == 1)

    assert (await grace.http.post("/api/v1/blocks", json={"user_id": ada.id})).status_code == 204
    ack = await ada.send(conversation, "after the block")
    assert ack["ok"] is False and ack["error"]["code"] == "not_found"
    await asyncio.sleep(0.3)
    assert len(grace.received()) == 1
    for s in (ada, grace):
        assert (await history(s, conversation)).status_code == 404
        assert (await s.http.get("/api/v1/conversations")).json() == []


async def test_a_deleted_accounts_messages_become_removed_for_the_others(
    trio: tuple[Student, Student, Student, str], clean_database: AsyncEngine
) -> None:
    ada, grace, _, conversation = trio
    await ada.open_socket()
    for body in ("one", "two"):
        await ada.send(conversation, body)

    deleted = await ada.http.request(
        "DELETE", "/api/v1/me", json={"password": "correct horse battery"}
    )
    assert deleted.status_code == 204
    page = (await history(grace, conversation)).json()
    assert [(m["removed"], m["body"], m["author_id"]) for m in page["messages"]] == [
        (True, None, None),
        (True, None, None),
    ]
    assert all(m["author_name"] is None for m in page["messages"])
    assert await scalar(clean_database, "SELECT count(*) FROM messages WHERE body IS NOT NULL") == 0

    # Ada is gone but Grace is still a member: the conversation survives (messages stay
    # readable), yet it's a direct conversation with nobody "other" left, so it drops out of
    # the list and its own page 404s, the same as before a member ever went missing.
    assert (await grace.http.get("/api/v1/conversations")).json() == []
    assert (await grace.http.get(f"/api/v1/conversations/{conversation}")).status_code == 404
    page_again = (await history(grace, conversation)).json()
    assert len(page_again["messages"]) == 2

    # Once nobody is left, the conversation itself goes.
    gone = await grace.http.request(
        "DELETE", "/api/v1/me", json={"password": "correct horse battery"}
    )
    assert gone.status_code == 204
    for table in ("conversations", "messages"):
        assert await scalar(clean_database, f'SELECT count(*) FROM "{table}"') == 0  # noqa: S608


async def test_every_socket_event_rechecks_the_session(
    trio: tuple[Student, Student, Student, str], clean_database: AsyncEngine
) -> None:
    ada, _, _, conversation = trio
    sock = await ada.open_socket()
    disconnected = asyncio.Event()
    sock.on("disconnect", lambda *_: disconnected.set())
    await execute(
        clean_database,
        "UPDATE sessions SET expires_at = now() - interval '1 second' WHERE user_id = :id",
        id=ada.id,
    )

    ack = await ada.send(conversation, "after expiry")
    assert ack["ok"] is False and ack["error"]["code"] == "unauthorized"
    async with asyncio.timeout(5):
        await disconnected.wait()
    assert await scalar(clean_database, "SELECT count(*) FROM messages") == 0


async def test_message_bodies_never_reach_the_logs(
    trio: tuple[Student, Student, Student, str], caplog: pytest.LogCaptureFixture
) -> None:
    ada, grace, _, conversation = trio
    await ada.open_socket()
    await grace.open_socket()
    secret = f"secret-{uuid.uuid4()}"
    with caplog.at_level(logging.DEBUG):
        await ada.send(conversation, secret)
        await eventually(lambda: grace.received())
        await history(grace, conversation)
    assert secret not in caplog.text


# --- conversations and presence -------------------------------------------------------------


async def test_the_conversation_list_shows_the_other_student_and_latest_message(
    trio: tuple[Student, Student, Student, str],
) -> None:
    ada, grace, eve, conversation = trio
    await ada.open_socket()
    await ada.send(conversation, "first")
    await ada.send(conversation, "latest")

    [item] = (await grace.http.get("/api/v1/conversations")).json()
    assert item["id"] == conversation and item["kind"] == "direct"
    assert (item["name"], item["project_id"]) == (None, None)
    assert (item["other"]["user_id"], item["other"]["display_name"], item["other"]["online"]) == (
        ada.id,
        "Ada",
        True,
    )
    assert item["last_message"]["body"] == "latest"
    assert (await eve.http.get("/api/v1/conversations")).json() == []
    assert "ada@northeastern.edu" not in str(item)


async def test_the_conversation_list_keeps_the_most_recently_active_ones_under_the_cap(
    live_server: str, mailer: MemoryMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chat_service, "MAX_CONVERSATIONS", 2)
    ada = await make_student(live_server, mailer, "ada")
    bea = await make_student(live_server, mailer, "bea")
    cleo = await make_student(live_server, mailer, "cleo")
    dana = await make_student(live_server, mailer, "dana")
    oldest = await connect_pair(ada, bea)
    middle = await connect_pair(ada, cleo)
    newest = await connect_pair(ada, dana)

    try:
        await ada.open_socket()
        # The oldest-created conversation gets the newest message, so it must survive the cap
        # even though it would otherwise be the first one Postgres drops.
        await ada.send(oldest, "bumped back to the top")

        ids = [item["id"] for item in (await ada.http.get("/api/v1/conversations")).json()]
        assert len(ids) == 2
        assert oldest in ids and newest in ids
        assert middle not in ids
    finally:
        for s in (ada, bea, cleo, dana):
            await s.close()


async def test_one_conversation_can_be_read_by_its_members_only(
    trio: tuple[Student, Student, Student, str],
) -> None:
    ada, grace, eve, conversation = trio
    response = await grace.http.get(f"/api/v1/conversations/{conversation}")
    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["other"]["user_id"], body["last_message"]) == (
        conversation,
        ada.id,
        None,
    )
    assert (body["kind"], body["name"], body["project_id"]) == ("direct", None, None)
    for target in (conversation, str(uuid.uuid4())):
        assert (await eve.http.get(f"/api/v1/conversations/{target}")).status_code == 404


async def test_presence_is_shared_only_with_people_in_a_conversation(
    trio: tuple[Student, Student, Student, str],
) -> None:
    ada, grace, eve, _ = trio
    await grace.open_socket()
    await eve.open_socket()

    ada_socket = await ada.open_socket()
    await eventually(lambda: grace.received("presence.changed"))
    assert grace.received("presence.changed") == [{"user_id": ada.id, "online": True}]

    await ada_socket.disconnect()
    await eventually(lambda: len(grace.received("presence.changed")) == 2)
    assert grace.received("presence.changed")[1] == {"user_id": ada.id, "online": False}
    await asyncio.sleep(0.2)
    assert eve.received("presence.changed") == []


async def test_messaging_routes_need_a_session(live_server: str) -> None:
    async with new_client(live_server) as anonymous:
        assert (await anonymous.get("/api/v1/conversations")).status_code == 401
        response = await anonymous.get(f"/api/v1/conversations/{uuid.uuid4()}/messages")
        assert response.status_code == 401
