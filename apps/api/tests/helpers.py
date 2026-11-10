"""Signing students up and in, shared by the API test modules."""

from typing import Any

import httpx
import socketio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.security import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from tests.conftest import WEB_ORIGIN, MemoryMailer

EMAIL = "ada@northeastern.edu"
PASSWORD = "correct horse battery"


async def register(
    client: httpx.AsyncClient, email: str = EMAIL, password: str = PASSWORD
) -> httpx.Response:
    return await client.post("/api/v1/auth/register", json={"email": email, "password": password})


async def verify_last(client: httpx.AsyncClient, mailer: MemoryMailer) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/verify", json={"token": mailer.token_from_last("/verify-email")}
    )


async def login(
    client: httpx.AsyncClient, email: str = EMAIL, password: str = PASSWORD
) -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def signed_in(
    client: httpx.AsyncClient, mailer: MemoryMailer, email: str = EMAIL
) -> httpx.Response:
    await register(client, email)
    await verify_last(client, mailer)
    response = await login(client, email)
    assert response.status_code == 200
    return response


def client_from(app: object, ip: str) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(ip, 50000))  # type: ignore[arg-type]
    return httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"Origin": WEB_ORIGIN}
    )


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    return {CSRF_HEADER: client.cookies[CSRF_COOKIE]}


async def scalar(engine: AsyncEngine, sql: str, **params: object) -> object:
    async with engine.connect() as connection:
        return await connection.scalar(text(sql), params)


async def execute(engine: AsyncEngine, sql: str, **params: object) -> None:
    async with engine.begin() as connection:
        await connection.execute(text(sql), params)


def new_client(app: object) -> httpx.AsyncClient:
    """Another browser on the same app (or live server URL): same origin, its own cookies."""
    if isinstance(app, str):
        return httpx.AsyncClient(base_url=app, headers={"Origin": WEB_ORIGIN})
    return client_from(app, "127.0.0.1")


async def student(
    app: object, mailer: MemoryMailer, email: str, *, verified: bool = True
) -> httpx.AsyncClient:
    """A signed-in student whose unsafe requests carry the CSRF header automatically."""
    client = new_client(app)
    await register(client, email)
    if verified:
        await verify_last(client, mailer)
    response = await login(client, email)
    assert response.status_code == 200, response.text
    client.headers.update(csrf(client))
    return client


async def user_id(client: httpx.AsyncClient) -> str:
    return str((await client.get("/api/v1/me")).json()["id"])


# A complete, valid profile (slugs from the taxonomy in migration 0003).
PROFILE: dict[str, Any] = {
    "display_name": "Ada Lovelace",
    "headline": "Analytical engines and poetry",
    "bio": "Looking for a team to build a music generator.\nEvenings work best.",
    "goals": ["research", "project_development"],
    "timezone": "America/New_York",
    "skills": [
        {"slug": "python", "level": "advanced"},
        {"slug": "machine-learning", "level": "intermediate"},
    ],
    "seeking_skills": ["ui-design"],
    "interests": ["music-and-arts", "education"],
    "availability": [
        {"weekday": 2, "start_min": 600, "end_min": 720},
        {"weekday": 0, "start_min": 1080, "end_min": 1260},
    ],
    "discoverable": True,
    "feedback_consent": False,
}


async def connect(url: str, origin: str, session: str | None) -> socketio.AsyncClient:
    # WebSocket closes immediately on disconnect; long-polling would hold a request open
    # for the full ping interval (~25 s). Both transports go through the same checks.
    headers = {"Origin": origin}
    if session is not None:
        headers["Cookie"] = f"{SESSION_COOKIE}={session}"
    client = socketio.AsyncClient(reconnection=False)
    await client.connect(url, headers=headers, transports=["websocket"], wait_timeout=5)
    return client
