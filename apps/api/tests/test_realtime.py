import asyncio

import httpx
import pytest
import socketio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.security import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from tests.conftest import WEB_ORIGIN, MemoryMailer
from tests.helpers import connect

pytestmark = pytest.mark.usefixtures("clean_database")

EMAIL = "ada@northeastern.edu"
PASSWORD = "correct horse battery"


async def sign_in(base_url: str, mailer: MemoryMailer) -> httpx.AsyncClient:
    client = httpx.AsyncClient(base_url=base_url, headers={"Origin": WEB_ORIGIN})
    await client.post("/api/v1/auth/register", json={"email": EMAIL, "password": PASSWORD})
    await client.post(
        "/api/v1/auth/verify", json={"token": mailer.token_from_last("/verify-email")}
    )
    response = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200
    return client


async def test_a_signed_in_client_from_the_web_origin_can_connect(
    live_server: str, mailer: MemoryMailer
) -> None:
    http = await sign_in(live_server, mailer)
    client = await connect(live_server, WEB_ORIGIN, http.cookies[SESSION_COOKIE])
    try:
        assert client.connected
    finally:
        await client.disconnect()
        await http.aclose()


async def test_connections_without_a_valid_session_are_refused(live_server: str) -> None:
    """Invalid socket sessions cannot connect."""
    for session in (None, "made-up-session-value"):
        with pytest.raises(socketio.exceptions.ConnectionError):
            await connect(live_server, WEB_ORIGIN, session)


async def test_a_client_from_another_origin_is_refused_even_with_a_session(
    live_server: str, mailer: MemoryMailer
) -> None:
    http = await sign_in(live_server, mailer)
    try:
        with pytest.raises(socketio.exceptions.ConnectionError):
            await connect(live_server, "http://evil.example", http.cookies[SESSION_COOKIE])
    finally:
        await http.aclose()


async def test_an_expired_session_cannot_connect(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    """Expired sessions fail safely on socket connect too."""
    http = await sign_in(live_server, mailer)
    async with clean_database.begin() as connection:
        await connection.execute(
            text("UPDATE sessions SET expires_at = now() - interval '1 second'")
        )
    try:
        with pytest.raises(socketio.exceptions.ConnectionError):
            await connect(live_server, WEB_ORIGIN, http.cookies[SESSION_COOKIE])
    finally:
        await http.aclose()


async def test_logging_out_disconnects_that_sessions_sockets(
    live_server: str, mailer: MemoryMailer
) -> None:
    http = await sign_in(live_server, mailer)
    client = await connect(live_server, WEB_ORIGIN, http.cookies[SESSION_COOKIE])
    disconnected = asyncio.Event()
    client.on("disconnect", lambda *_: disconnected.set())

    await http.post("/api/v1/auth/logout", headers={CSRF_HEADER: http.cookies[CSRF_COOKIE]})

    async with asyncio.timeout(5):
        await disconnected.wait()
    assert not client.connected
    await http.aclose()
