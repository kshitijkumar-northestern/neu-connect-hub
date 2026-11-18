"""Size caps at the default settings: an HTTP body over the limit gets 413 before anything is
parsed or written, and a Socket.IO frame over the limit ends the connection before any handler
sees it, over WebSocket and long-polling alike."""

import asyncio
import json
import uuid
from typing import Any

import pytest
import socketio

from app.core.security import SESSION_COOKIE
from app.main import create_app
from app.modules.rooms.signals import SDP_MAX
from tests.conftest import WEB_ORIGIN, MemoryMailer, make_settings
from tests.helpers import connect, profile, student

pytestmark = pytest.mark.usefixtures("clean_database")


def chat_payload(body: str) -> dict[str, str]:
    return {
        "conversation_id": str(uuid.uuid4()),
        "client_msg_id": str(uuid.uuid4()),
        "body": body,
    }


async def signed_in_socket(live_server: str, mailer: MemoryMailer) -> socketio.AsyncClient:
    http = await student(live_server, mailer, "ada@northeastern.edu")
    try:
        return await connect(live_server, WEB_ORIGIN, http.cookies[SESSION_COOKIE])
    finally:
        await http.aclose()


async def disconnected(sock: socketio.AsyncClient, within: float = 5) -> bool:
    try:
        async with asyncio.timeout(within):
            while sock.connected:  # noqa: ASYNC110 - the client exposes a flag, not an event
                await asyncio.sleep(0.02)
    except TimeoutError:
        return False
    return True


async def test_a_profile_save_over_the_default_body_cap_is_refused_and_writes_nothing(
    app: object, mailer: MemoryMailer
) -> None:
    limit = make_settings().max_request_body_bytes
    client = await student(app, mailer, "ada@northeastern.edu")
    try:
        # Valid JSON for the route, padded past the cap inside a field the schema would refuse
        # anyway: the size check must answer first.
        body = json.dumps(profile(bio="x" * limit)).encode()
        assert len(body) > limit
        response = await client.put(
            "/api/v1/me/profile", content=body, headers={"Content-Type": "application/json"}
        )
        saved = await client.get("/api/v1/me/profile")
    finally:
        await client.aclose()

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert saved.status_code == 404


async def test_a_profile_save_just_under_the_default_body_cap_reaches_the_route(
    app: object, mailer: MemoryMailer
) -> None:
    limit = make_settings().max_request_body_bytes
    client = await student(app, mailer, "ada@northeastern.edu")
    try:
        body = json.dumps(profile()).encode()
        padded = body[:-1] + b" " * (limit - len(body)) + b"}"
        assert len(padded) == limit
        response = await client.put(
            "/api/v1/me/profile", content=padded, headers={"Content-Type": "application/json"}
        )
    finally:
        await client.aclose()

    assert response.status_code == 200, response.text


def test_the_default_frame_cap_is_64_kib_and_fits_the_largest_signal() -> None:
    limit = make_settings().socket_max_message_bytes

    assert limit == 64 * 1024
    # An offer at the schema's SDP limit, with its JSON framing, must still get through.
    assert limit > SDP_MAX + 1024


def test_the_cap_is_handed_to_the_engine_io_server() -> None:
    app = create_app(make_settings(socket_max_message_bytes=40_000))

    sio_app: Any = app.app  # the headers layer wraps the Socket.IO app

    assert sio_app.engineio_server.eio.max_http_buffer_size == 40_000


async def test_an_oversized_websocket_frame_disconnects_the_socket(
    live_server: str, mailer: MemoryMailer
) -> None:
    sock = await signed_in_socket(live_server, mailer)
    try:
        # A frame within the cap reaches the handler, which answers with an ack.
        ack = await sock.call("chat.send", chat_payload("hello"), timeout=5)
        assert ack["ok"] is False and ack["error"]["code"] != "unauthorized"

        await sock.emit("chat.send", chat_payload("x" * (64 * 1024 + 1)))

        assert await disconnected(sock)
    finally:
        if sock.connected:
            await sock.disconnect()


async def test_an_oversized_polling_body_disconnects_the_socket(
    live_server: str, mailer: MemoryMailer
) -> None:
    http = await student(live_server, mailer, "ada@northeastern.edu")
    sock = socketio.AsyncClient(reconnection=False)
    try:
        # The handshake announces the cap, so the browser client batches below it.
        handshake = await http.get("/socket.io/", params={"EIO": "4", "transport": "polling"})
        assert json.loads(handshake.text.removeprefix("0"))["maxPayload"] == 64 * 1024

        cookie = f"{SESSION_COOKIE}={http.cookies[SESSION_COOKIE]}"
        await sock.connect(
            live_server,
            headers={"Origin": WEB_ORIGIN, "Cookie": cookie},
            transports=["polling"],
            wait_timeout=5,
        )
        await sock.emit("chat.send", chat_payload("x" * (64 * 1024 + 1)))

        assert await disconnected(sock, within=10)
    finally:
        if sock.connected:
            await sock.disconnect()
        await http.aclose()
