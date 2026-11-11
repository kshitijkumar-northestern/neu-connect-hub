"""Socket.IO signaling for calls: room.join, rtc.signal and room.leave. Rooms are created with
raw SQL here, since the HTTP routes that will normally create them are built elsewhere."""

import asyncio
import logging
import socket as socket_module
import uuid
from typing import Any

import pytest
import socketio
import uvicorn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.rate_limit import Limit
from app.core.security import SESSION_COOKIE
from app.main import create_http_app
from app.realtime import room_events
from app.realtime.calls import CallRegistry
from app.realtime.server import create_socketio_server
from tests.conftest import WEB_ORIGIN, MemoryMailer
from tests.helpers import (
    Student,
    connect,
    connect_pair,
    eventually,
    execute,
    make_project_chat,
    make_student,
    scalar,
)

pytestmark = pytest.mark.usefixtures("clean_database")

OFFER = {"type": "offer", "sdp": "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n"}
CANDIDATE = {
    "type": "candidate",
    "candidate": {
        "candidate": "candidate:1 1 UDP 2122260223 192.0.2.1 54321 typ host",
        "sdpMid": "0",
        "sdpMLineIndex": 0,
        "usernameFragment": "abc123",
    },
}


async def make_room(engine: AsyncEngine, conversation_id: str, created_by: str) -> str:
    room_id = str(uuid.uuid4())
    await execute(
        engine,
        "INSERT INTO rooms (id, conversation_id, created_by) VALUES (:id, :cid, :uid)",
        id=room_id,
        cid=conversation_id,
        uid=created_by,
    )
    return room_id


async def wait_for_room_state(
    engine: AsyncEngine, room_id: str, expected: str, within: float = 5
) -> None:
    async with asyncio.timeout(within):
        while True:
            state = await scalar(engine, "SELECT room_state FROM rooms WHERE id = :id", id=room_id)
            if state == expected:
                return
            await asyncio.sleep(0.02)


async def open_room_socket(s: Student) -> socketio.AsyncClient:
    sock = await connect(s.base_url, WEB_ORIGIN, s.http.cookies[SESSION_COOKIE])
    for event in ("room.peer_joined", "room.peer_left", "rtc.signal", "room.removed"):
        sock.on(event, lambda data, event=event: s.events.append((event, data)))
    s.socket = sock
    return sock


async def join(sock: socketio.AsyncClient, room_id: str) -> Any:
    return await sock.call("room.join", {"room_id": room_id}, timeout=5)


async def leave(sock: socketio.AsyncClient, room_id: str) -> Any:
    return await sock.call("room.leave", {"room_id": room_id}, timeout=5)


class InstrumentedServer:
    """Like the `live_server` fixture, but also exposes the process's own CallRegistry, so a
    test can tell whether a join has touched it before its transaction actually commits."""

    def __init__(
        self,
        base_url: str,
        calls: CallRegistry,
        server: uvicorn.Server,
        task: asyncio.Task[None],
        sock: socket_module.socket,
    ):
        self.base_url = base_url
        self.calls = calls
        self.server = server
        self.task = task
        self.sock = sock


async def start_instrumented_server(settings: Settings, mailer: MemoryMailer) -> InstrumentedServer:
    http_app = create_http_app(settings, mailer=mailer)
    sio, rooms_hooks = create_socketio_server(
        settings,
        http_app.state.db,
        http_app.state.limiter,
        http_app.state.presence,
        http_app.state.calls,
    )
    http_app.state.sio = sio
    http_app.state.room_access = rooms_hooks.recheck_room_access
    asgi_app = socketio.ASGIApp(sio, other_asgi_app=http_app)

    sock = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    config = uvicorn.Config(asgi_app, log_level="warning", timeout_graceful_shutdown=2)
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(sockets=[sock]))
    async with asyncio.timeout(10):
        while not server.started:  # noqa: ASYNC110 - uvicorn exposes a flag, not an event
            await asyncio.sleep(0.01)
    host, port = sock.getsockname()
    return InstrumentedServer(f"http://{host}:{port}", http_app.state.calls, server, task, sock)


async def stop_instrumented_server(instance: InstrumentedServer) -> None:
    instance.server.should_exit = True
    await instance.task
    instance.sock.close()


async def rtc_signal(
    sock: socketio.AsyncClient, room_id: str, to: str, signal: dict[str, Any]
) -> Any:
    return await sock.call(
        "rtc.signal", {"room_id": room_id, "to": to, "signal": signal}, timeout=5
    )


# --- joining ---------------------------------------------------------------------------------


async def test_a_non_member_cannot_join_and_bad_ids_get_a_safe_ack(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await make_student(live_server, mailer, "ada")
    grace = await make_student(live_server, mailer, "grace")
    eve = await make_student(live_server, mailer, "eve")
    conversation = await connect_pair(ada, grace)
    room_id = await make_room(clean_database, conversation, ada.id)

    eve_conversation = await connect_pair(ada, eve)
    deleted_room_id = await make_room(clean_database, eve_conversation, ada.id)
    await execute(clean_database, "DELETE FROM rooms WHERE id = :id", id=deleted_room_id)

    try:
        sock = await open_room_socket(eve)

        ack_non_member = await join(sock, room_id)
        assert ack_non_member == {
            "ok": False,
            "error": {"code": "not_found", "message": ack_non_member["error"]["message"]},
        }

        ack_malformed = await sock.call("room.join", {"room_id": "not-a-uuid"}, timeout=5)
        assert ack_malformed["ok"] is False
        assert ack_malformed["error"]["code"] == "validation_error"

        ack_unknown = await join(sock, str(uuid.uuid4()))
        assert ack_unknown["ok"] is False and ack_unknown["error"]["code"] == "not_found"

        ack_deleted = await join(sock, deleted_room_id)
        assert ack_deleted["ok"] is False and ack_deleted["error"]["code"] == "not_found"

        # The server kept serving: a legitimate join on the very same socket still succeeds.
        legit_room = await make_room(clean_database, eve_conversation, ada.id)
        ack_ok = await join(sock, legit_room)
        assert ack_ok["ok"] is True
    finally:
        for s in (ada, grace, eve):
            await s.close()


async def test_join_returns_existing_peers_and_notifies_them(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await make_student(live_server, mailer, "ada")
    grace = await make_student(live_server, mailer, "grace")
    conversation = await connect_pair(ada, grace)
    room_id = await make_room(clean_database, conversation, ada.id)

    try:
        await open_room_socket(ada)
        await open_room_socket(grace)

        ack_ada = await join(ada.socket, room_id)
        assert ack_ada == {"ok": True, "room_id": room_id, "peers": []}

        ack_grace = await join(grace.socket, room_id)
        assert ack_grace["ok"] is True
        assert ack_grace["peers"] == [{"user_id": ada.id, "display_name": "Ada"}]

        await eventually(lambda: ada.received("room.peer_joined"))
        assert ada.received("room.peer_joined") == [
            {"room_id": room_id, "peer": {"user_id": grace.id, "display_name": "Grace"}}
        ]
    finally:
        for s in (ada, grace):
            await s.close()


async def test_joining_a_full_room_is_refused_and_a_second_socket_takes_over(
    live_server: str,
    mailer: MemoryMailer,
    clean_database: AsyncEngine,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "room_capacity", 2)
    ada = await make_student(live_server, mailer, "ada")
    grace = await make_student(live_server, mailer, "grace")
    eve = await make_student(live_server, mailer, "eve")
    _, team_chat = await make_project_chat(clean_database, ada.id, [grace.id, eve.id])
    room_id = await make_room(clean_database, team_chat, ada.id)

    try:
        for s in (ada, grace, eve):
            await open_room_socket(s)

        assert (await join(ada.socket, room_id))["ok"] is True
        assert (await join(grace.socket, room_id))["ok"] is True

        ack_full = await join(eve.socket, room_id)
        assert ack_full == {
            "ok": False,
            "error": {"code": "room_full", "message": ack_full["error"]["message"]},
        }

        old_ada_socket = ada.socket
        ada_second = await connect(live_server, WEB_ORIGIN, ada.http.cookies[SESSION_COOKIE])
        try:
            ack_takeover = await ada_second.call("room.join", {"room_id": room_id}, timeout=5)
            assert ack_takeover["ok"] is True
            assert ack_takeover["peers"] == [{"user_id": grace.id, "display_name": "Grace"}]

            await eventually(lambda: ada.received("room.removed"))
            assert ada.received("room.removed") == [
                {"room_id": room_id, "reason": "joined_elsewhere"}
            ]

            await eventually(lambda: grace.received("room.peer_left"))
            assert grace.received("room.peer_left")[-1] == {
                "room_id": room_id,
                "user_id": ada.id,
            }
            await eventually(lambda: grace.received("room.peer_joined"))
            assert grace.received("room.peer_joined")[-1] == {
                "room_id": room_id,
                "peer": {"user_id": ada.id, "display_name": "Ada"},
            }

            # The old socket is no longer the registered one: its signals are refused.
            assert old_ada_socket is not None
            old_signal_ack = await rtc_signal(old_ada_socket, room_id, grace.id, OFFER)
            assert old_signal_ack["ok"] is False
            assert old_signal_ack["error"]["code"] == "not_in_room"

            rows = await scalar(
                clean_database,
                "SELECT count(*) FROM room_members WHERE room_id = :r AND user_id = :u",
                r=room_id,
                u=ada.id,
            )
            assert rows == 2
            assert (
                await scalar(
                    clean_database, "SELECT room_state FROM rooms WHERE id = :id", id=room_id
                )
                == "active"
            )
        finally:
            await ada_second.disconnect()
    finally:
        for s in (ada, grace, eve):
            await s.close()


# --- signaling -------------------------------------------------------------------------------


async def test_signals_relay_only_between_students_in_the_same_room(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await make_student(live_server, mailer, "ada")
    grace = await make_student(live_server, mailer, "grace")
    frank = await make_student(live_server, mailer, "frank")
    eve = await make_student(live_server, mailer, "eve")
    conversation_a = await connect_pair(ada, grace)
    room_a = await make_room(clean_database, conversation_a, ada.id)
    conversation_b = await connect_pair(frank, eve)
    room_b = await make_room(clean_database, conversation_b, frank.id)

    try:
        for s in (ada, grace, frank, eve):
            await open_room_socket(s)

        # ada hasn't joined room_a yet.
        ack_not_in_room = await rtc_signal(ada.socket, room_a, grace.id, OFFER)
        assert ack_not_in_room == {
            "ok": False,
            "error": {"code": "not_in_room", "message": ack_not_in_room["error"]["message"]},
        }

        assert (await join(ada.socket, room_a))["ok"] is True
        assert (await join(grace.socket, room_a))["ok"] is True
        assert (await join(frank.socket, room_b))["ok"] is True
        assert (await join(eve.socket, room_b))["ok"] is True

        ack_missing_target = await rtc_signal(ada.socket, room_a, frank.id, OFFER)
        assert ack_missing_target["ok"] is False
        assert ack_missing_target["error"]["code"] == "peer_not_found"

        ack_relay = await rtc_signal(ada.socket, room_a, grace.id, OFFER)
        assert ack_relay == {"ok": True}
        await eventually(lambda: grace.received("rtc.signal"))
        assert grace.received("rtc.signal") == [
            {"room_id": room_a, "from": ada.id, "signal": OFFER}
        ]
        await asyncio.sleep(0.2)
        assert frank.received("rtc.signal") == []
        assert eve.received("rtc.signal") == []

        ack_candidate = await rtc_signal(grace.socket, room_a, ada.id, CANDIDATE)
        assert ack_candidate == {"ok": True}
        await eventually(lambda: ada.received("rtc.signal"))
        assert ada.received("rtc.signal") == [
            {"room_id": room_a, "from": grace.id, "signal": CANDIDATE}
        ]

        # A second socket of ada's isn't the one registered for this room.
        ada_second = await connect(live_server, WEB_ORIGIN, ada.http.cookies[SESSION_COOKIE])
        try:
            ack_wrong_socket = await ada_second.call(
                "rtc.signal", {"room_id": room_a, "to": grace.id, "signal": OFFER}, timeout=5
            )
            assert ack_wrong_socket["ok"] is False
            assert ack_wrong_socket["error"]["code"] == "not_in_room"
        finally:
            await ada_second.disconnect()
    finally:
        for s in (ada, grace, frank, eve):
            await s.close()


async def test_a_block_during_a_call_removes_both_and_ends_it(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    """The block route pushes both students out immediately: no signal is needed."""
    ada = await make_student(live_server, mailer, "ada")
    grace = await make_student(live_server, mailer, "grace")
    conversation = await connect_pair(ada, grace)
    room_id = await make_room(clean_database, conversation, ada.id)

    try:
        await open_room_socket(ada)
        await open_room_socket(grace)
        assert (await join(ada.socket, room_id))["ok"] is True
        assert (await join(grace.socket, room_id))["ok"] is True

        assert (
            await grace.http.post("/api/v1/blocks", json={"user_id": ada.id})
        ).status_code == 204

        await eventually(lambda: ada.received("room.removed"))
        assert ada.received("room.removed") == [{"room_id": room_id, "reason": "access_lost"}]
        await eventually(lambda: grace.received("room.removed"))
        assert grace.received("room.removed") == [{"room_id": room_id, "reason": "access_lost"}]

        assert (
            await scalar(clean_database, "SELECT room_state FROM rooms WHERE id = :id", id=room_id)
            == "ended"
        )
        for user_id in (ada.id, grace.id):
            left_at = await scalar(
                clean_database,
                "SELECT left_at FROM room_members WHERE room_id = :r AND user_id = :u",
                r=room_id,
                u=user_id,
            )
            assert left_at is not None
    finally:
        for s in (ada, grace):
            await s.close()


async def test_an_owner_blocking_a_team_member_removes_only_them_from_the_call(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    """Blocking is also how a student is removed from a team: only the removed one leaves the
    call, pushed out with no signal needed; a third teammate stays in."""
    owner = await make_student(live_server, mailer, "owner")
    member = await make_student(live_server, mailer, "member")
    other = await make_student(live_server, mailer, "other")
    _, team_chat = await make_project_chat(clean_database, owner.id, [member.id, other.id])
    room_id = await make_room(clean_database, team_chat, owner.id)

    try:
        for s in (owner, member, other):
            await open_room_socket(s)
        for s in (owner, member, other):
            assert (await join(s.socket, room_id))["ok"] is True

        assert (
            await owner.http.post("/api/v1/blocks", json={"user_id": member.id})
        ).status_code == 204

        await eventually(lambda: member.received("room.removed"))
        assert member.received("room.removed") == [{"room_id": room_id, "reason": "access_lost"}]

        await eventually(lambda: owner.received("room.peer_left"))
        assert {"room_id": room_id, "user_id": member.id} in owner.received("room.peer_left")
        await eventually(lambda: other.received("room.peer_left"))
        assert {"room_id": room_id, "user_id": member.id} in other.received("room.peer_left")

        assert (
            await scalar(clean_database, "SELECT room_state FROM rooms WHERE id = :id", id=room_id)
            == "active"
        )
        assert owner.received("room.removed") == []
        assert other.received("room.removed") == []
    finally:
        for s in (owner, member, other):
            await s.close()


async def test_losing_team_membership_during_a_call_ends_access(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    owner = await make_student(live_server, mailer, "owner")
    member = await make_student(live_server, mailer, "member")
    other = await make_student(live_server, mailer, "other")
    _, team_chat = await make_project_chat(clean_database, owner.id, [member.id, other.id])
    room_id = await make_room(clean_database, team_chat, owner.id)

    try:
        for s in (owner, member, other):
            await open_room_socket(s)
        for s in (owner, member, other):
            assert (await join(s.socket, room_id))["ok"] is True

        await execute(
            clean_database,
            "UPDATE conversation_members SET active = false "
            "WHERE conversation_id = :cid AND user_id = :uid",
            cid=team_chat,
            uid=member.id,
        )

        ack = await rtc_signal(member.socket, room_id, owner.id, OFFER)
        assert ack["ok"] is False and ack["error"]["code"] == "not_found"

        await eventually(lambda: owner.received("room.peer_left"))
        assert {"room_id": room_id, "user_id": member.id} in owner.received("room.peer_left")
        await eventually(lambda: other.received("room.peer_left"))
        assert {"room_id": room_id, "user_id": member.id} in other.received("room.peer_left")
    finally:
        for s in (owner, member, other):
            await s.close()


async def test_signals_are_rate_limited(
    live_server: str,
    mailer: MemoryMailer,
    clean_database: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(room_events, "SIGNALS_PER_USER", Limit(max_events=2, window_seconds=60))
    ada = await make_student(live_server, mailer, "ada")
    grace = await make_student(live_server, mailer, "grace")
    conversation = await connect_pair(ada, grace)
    room_id = await make_room(clean_database, conversation, ada.id)

    try:
        await open_room_socket(ada)
        await open_room_socket(grace)
        assert (await join(ada.socket, room_id))["ok"] is True
        assert (await join(grace.socket, room_id))["ok"] is True

        acks = [await rtc_signal(ada.socket, room_id, grace.id, OFFER) for _ in range(3)]
        assert [a["ok"] for a in acks] == [True, True, False]
        assert acks[2]["error"]["code"] == "rate_limited"
    finally:
        for s in (ada, grace):
            await s.close()


async def test_sdp_never_reaches_the_logs(
    live_server: str,
    mailer: MemoryMailer,
    clean_database: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ada = await make_student(live_server, mailer, "ada")
    grace = await make_student(live_server, mailer, "grace")
    conversation = await connect_pair(ada, grace)
    room_id = await make_room(clean_database, conversation, ada.id)
    secret = f"v=0 secret-{uuid.uuid4()}"

    try:
        await open_room_socket(ada)
        await open_room_socket(grace)
        assert (await join(ada.socket, room_id))["ok"] is True
        assert (await join(grace.socket, room_id))["ok"] is True

        with caplog.at_level(logging.DEBUG):
            await rtc_signal(ada.socket, room_id, grace.id, {"type": "offer", "sdp": secret})
            await eventually(lambda: grace.received("rtc.signal"))
            # An invalid signal (sdp too short) must not echo it back either.
            await ada.socket.call(
                "rtc.signal",
                {"room_id": room_id, "to": grace.id, "signal": {"type": "offer", "sdp": ""}},
                timeout=5,
            )
        assert secret not in caplog.text
    finally:
        for s in (ada, grace):
            await s.close()


# --- leaving ---------------------------------------------------------------------------------


async def test_leave_and_disconnect_notify_close_attendance_and_end_the_room(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await make_student(live_server, mailer, "ada")
    grace = await make_student(live_server, mailer, "grace")
    conversation = await connect_pair(ada, grace)
    room_id = await make_room(clean_database, conversation, ada.id)

    try:
        await open_room_socket(ada)
        await open_room_socket(grace)
        assert (await join(ada.socket, room_id))["ok"] is True
        assert (await join(grace.socket, room_id))["ok"] is True

        ack_leave = await leave(ada.socket, room_id)
        assert ack_leave == {"ok": True}
        await eventually(lambda: grace.received("room.peer_left"))
        assert grace.received("room.peer_left") == [{"room_id": room_id, "user_id": ada.id}]

        left_at = await scalar(
            clean_database,
            "SELECT left_at FROM room_members WHERE room_id = :r AND user_id = :u",
            r=room_id,
            u=ada.id,
        )
        assert left_at is not None
        assert (
            await scalar(clean_database, "SELECT room_state FROM rooms WHERE id = :id", id=room_id)
            == "active"
        )

        # Leaving twice (idempotent) is still a plain ok.
        assert (await leave(ada.socket, room_id)) == {"ok": True}

        assert grace.socket is not None
        await grace.socket.disconnect()
        await wait_for_room_state(clean_database, room_id, "ended")
        ended_at = await scalar(
            clean_database, "SELECT ended_at FROM rooms WHERE id = :id", id=room_id
        )
        assert ended_at is not None

        ack_rejoin = await join(ada.socket, room_id)
        assert ack_rejoin == {
            "ok": False,
            "error": {"code": "room_ended", "message": ack_rejoin["error"]["message"]},
        }
    finally:
        for s in (ada, grace):
            await s.close()


async def test_leave_from_the_wrong_socket_does_not_evict_the_registered_one(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    """A stale tab of the same student can never evict the tab that's actually in the call."""
    ada = await make_student(live_server, mailer, "ada")
    grace = await make_student(live_server, mailer, "grace")
    conversation = await connect_pair(ada, grace)
    room_id = await make_room(clean_database, conversation, ada.id)

    try:
        await open_room_socket(ada)
        await open_room_socket(grace)
        assert (await join(ada.socket, room_id))["ok"] is True
        assert (await join(grace.socket, room_id))["ok"] is True

        ada_tab_b = await connect(live_server, WEB_ORIGIN, ada.http.cookies[SESSION_COOKIE])
        try:
            ack = await ada_tab_b.call("room.leave", {"room_id": room_id}, timeout=5)
            assert ack == {"ok": True}

            await asyncio.sleep(0.2)
            assert grace.received("room.peer_left") == []
            # ada's registered socket (tab A) is still in the call.
            ack_signal = await rtc_signal(ada.socket, room_id, grace.id, OFFER)
            assert ack_signal == {"ok": True}
            assert (
                await scalar(
                    clean_database, "SELECT room_state FROM rooms WHERE id = :id", id=room_id
                )
                == "active"
            )
        finally:
            await ada_tab_b.disconnect()
    finally:
        for s in (ada, grace):
            await s.close()


async def test_disconnect_still_updates_presence_when_leaving_a_call_fails(
    live_server: str,
    mailer: MemoryMailer,
    clean_database: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database error while leaving a call on disconnect must never leak the student's
    presence to the rest of the app."""

    async def boom(*args: Any, **kwargs: Any) -> list[str]:
        raise RuntimeError("boom")

    monkeypatch.setattr(room_events, "leave_room", boom)

    ada = await make_student(live_server, mailer, "ada")
    grace = await make_student(live_server, mailer, "grace")
    conversation = await connect_pair(ada, grace)
    room_id = await make_room(clean_database, conversation, ada.id)

    try:
        await grace.open_socket()
        await open_room_socket(ada)
        assert (await join(ada.socket, room_id))["ok"] is True

        await eventually(lambda: grace.received("presence.changed"))
        grace.events.clear()

        assert ada.socket is not None
        await ada.socket.disconnect()

        await eventually(lambda: grace.received("presence.changed"))
        assert grace.received("presence.changed") == [{"user_id": ada.id, "online": False}]
    finally:
        for s in (ada, grace):
            await s.close()


async def test_a_join_racing_the_last_leave_never_lands_in_an_ended_room(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await make_student(live_server, mailer, "ada")
    grace = await make_student(live_server, mailer, "grace")
    eve = await make_student(live_server, mailer, "eve")
    _, team_chat = await make_project_chat(clean_database, ada.id, [grace.id, eve.id])
    room_id = await make_room(clean_database, team_chat, ada.id)

    try:
        await open_room_socket(grace)
        await open_room_socket(eve)
        assert (await join(grace.socket, room_id))["ok"] is True

        async with clean_database.connect() as lock_connection:
            lock_transaction = await lock_connection.begin()
            await lock_connection.execute(
                text("SELECT id FROM rooms WHERE id = :id FOR UPDATE"), {"id": room_id}
            )

            join_task = asyncio.ensure_future(join(eve.socket, room_id))
            leave_task = asyncio.ensure_future(leave(grace.socket, room_id))
            await asyncio.sleep(0.3)  # let both block on the row lock before releasing it
            await lock_transaction.commit()

        join_ack, leave_ack = await asyncio.gather(join_task, leave_task)
        assert leave_ack == {"ok": True}

        state = await scalar(
            clean_database, "SELECT room_state FROM rooms WHERE id = :id", id=room_id
        )
        if join_ack["ok"]:
            # Eve's join ran first: grace was still there when it computed its peer list.
            assert state == "active"
            assert join_ack["peers"] == [{"user_id": grace.id, "display_name": "Grace"}]
        else:
            # Grace's leave ran first and emptied the room before eve's join saw it.
            assert join_ack["error"]["code"] == "room_ended"
            assert state == "ended"
            retry = await join(eve.socket, room_id)
            assert retry["ok"] is False and retry["error"]["code"] == "room_ended"
    finally:
        for s in (ada, grace, eve):
            await s.close()


async def test_join_does_not_touch_the_registry_before_it_commits(
    settings: Settings, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    """The room row lock is taken before the registry is even read, not just before the
    attendance insert: a join that's still waiting for the lock must not have reserved a slot
    yet, or two joins could both pass the capacity check for the same free one."""
    instance = await start_instrumented_server(settings, mailer)
    ada = await make_student(instance.base_url, mailer, "ada")
    grace = await make_student(instance.base_url, mailer, "grace")
    conversation = await connect_pair(ada, grace)
    room_id = await make_room(clean_database, conversation, ada.id)

    try:
        await open_room_socket(ada)

        async with clean_database.connect() as lock_connection:
            lock_transaction = await lock_connection.begin()
            await lock_connection.execute(
                text("SELECT id FROM rooms WHERE id = :id FOR UPDATE"), {"id": room_id}
            )
            join_task = asyncio.ensure_future(join(ada.socket, room_id))
            await asyncio.sleep(0.3)
            assert not join_task.done()
            assert instance.calls.count(uuid.UUID(room_id)) == 0
            await lock_transaction.commit()

        ack = await join_task
        assert ack["ok"] is True
        assert instance.calls.count(uuid.UUID(room_id)) == 1
    finally:
        for s in (ada, grace):
            await s.close()
        await stop_instrumented_server(instance)


async def test_joining_blocks_on_the_room_row_lock(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    """A join in progress holds the room row lock, so a concurrent writer of that row (here, a
    second Postgres connection) delays it until the lock is free."""
    ada = await make_student(live_server, mailer, "ada")
    grace = await make_student(live_server, mailer, "grace")
    conversation = await connect_pair(ada, grace)
    room_id = await make_room(clean_database, conversation, ada.id)

    try:
        await open_room_socket(ada)
        async with clean_database.connect() as lock_connection:
            lock_transaction = await lock_connection.begin()
            await lock_connection.execute(
                text("SELECT id FROM rooms WHERE id = :id FOR UPDATE"), {"id": room_id}
            )
            join_task = asyncio.ensure_future(join(ada.socket, room_id))
            await asyncio.sleep(0.3)
            assert not join_task.done()
            await lock_transaction.commit()

        ack = await join_task
        assert ack["ok"] is True
    finally:
        for s in (ada, grace):
            await s.close()


async def test_leaving_blocks_on_the_room_row_lock(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    """The leave procedure holds the same lock, so a concurrent writer of the room row delays
    it too."""
    ada = await make_student(live_server, mailer, "ada")
    grace = await make_student(live_server, mailer, "grace")
    conversation = await connect_pair(ada, grace)
    room_id = await make_room(clean_database, conversation, ada.id)

    try:
        await open_room_socket(ada)
        assert (await join(ada.socket, room_id))["ok"] is True

        async with clean_database.connect() as lock_connection:
            lock_transaction = await lock_connection.begin()
            await lock_connection.execute(
                text("SELECT id FROM rooms WHERE id = :id FOR UPDATE"), {"id": room_id}
            )
            leave_task = asyncio.ensure_future(leave(ada.socket, room_id))
            await asyncio.sleep(0.3)
            assert not leave_task.done()
            await lock_transaction.commit()

        ack = await leave_task
        assert ack == {"ok": True}
    finally:
        for s in (ada, grace):
            await s.close()


# --- sessions ----------------------------------------------------------------------------------


async def test_an_expired_session_gets_unauthorized_and_disconnects(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await make_student(live_server, mailer, "ada")
    grace = await make_student(live_server, mailer, "grace")
    conversation = await connect_pair(ada, grace)
    room_id = await make_room(clean_database, conversation, ada.id)

    try:
        sock = await open_room_socket(ada)
        disconnected = asyncio.Event()
        sock.on("disconnect", lambda *_: disconnected.set())
        await execute(
            clean_database,
            "UPDATE sessions SET expires_at = now() - interval '1 second' WHERE user_id = :id",
            id=ada.id,
        )

        ack = await join(sock, room_id)
        assert ack == {
            "ok": False,
            "error": {"code": "unauthorized", "message": ack["error"]["message"]},
        }
        async with asyncio.timeout(5):
            await disconnected.wait()
    finally:
        for s in (ada, grace):
            await s.close()
