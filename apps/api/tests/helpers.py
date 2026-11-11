"""Signing students up and in, shared by the API test modules."""

import asyncio
import uuid
from dataclasses import dataclass, field
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


@dataclass
class Student:
    http: httpx.AsyncClient
    id: str
    base_url: str
    events: list[tuple[str, Any]] = field(default_factory=list)
    socket: socketio.AsyncClient | None = None

    async def open_socket(self) -> socketio.AsyncClient:
        sock = await connect(self.base_url, WEB_ORIGIN, self.http.cookies[SESSION_COOKIE])
        for event in ("chat.message", "presence.changed"):
            sock.on(event, lambda data, event=event: self.events.append((event, data)))
        self.socket = sock
        return sock

    async def send(self, conversation_id: str, body: str, client_msg_id: str | None = None) -> Any:
        assert self.socket is not None
        payload = {
            "conversation_id": conversation_id,
            "client_msg_id": client_msg_id or str(uuid.uuid4()),
            "body": body,
        }
        return await self.socket.call("chat.send", payload, timeout=5)

    def received(self, event: str = "chat.message") -> list[Any]:
        return [data for name, data in self.events if name == event]

    async def close(self) -> None:
        if self.socket is not None and self.socket.connected:
            await self.socket.disconnect()
        await self.http.aclose()


async def make_student(base_url: str, mailer: MemoryMailer, name: str) -> Student:
    http = await student(base_url, mailer, f"{name}@northeastern.edu")
    response = await http.put("/api/v1/me/profile", json={**PROFILE, "display_name": name.title()})
    assert response.status_code == 200, response.text
    return Student(http=http, id=await user_id(http), base_url=base_url)


async def connect_pair(a: Student, b: Student) -> str:
    sent = (await a.http.post("/api/v1/connections", json={"user_id": b.id})).json()
    accepted = (await b.http.post(f"/api/v1/connections/{sent['id']}/accept")).json()
    return str(accepted["conversation_id"])


async def eventually(predicate: Any, within: float = 5) -> None:
    async with asyncio.timeout(within):
        while not predicate():  # noqa: ASYNC110 - polling a list filled by socket callbacks
            await asyncio.sleep(0.02)


async def history(s: Student, conversation_id: str, **params: Any) -> httpx.Response:
    return await s.http.get(f"/api/v1/conversations/{conversation_id}/messages", params=params)


async def make_project_chat(
    engine: AsyncEngine,
    owner_id: str,
    member_ids: list[str],
    *,
    title: str = "Team project",
    capacity: int = 10,
    status: str = "open",
) -> tuple[str, str]:
    """Creates a project, its team chat and memberships straight in the database, the way the
    teams routes will once they exist. Returns (project_id, conversation_id)."""
    project_id = str(uuid.uuid4())
    conversation_id = str(uuid.uuid4())
    await execute(
        engine,
        "INSERT INTO projects (id, owner_id, title, capacity, status) "
        "VALUES (:id, :owner_id, :title, :capacity, :status)",
        id=project_id,
        owner_id=owner_id,
        title=title,
        capacity=capacity,
        status=status,
    )
    await execute(
        engine,
        "INSERT INTO conversations (id, kind, project_id) VALUES (:id, 'project', :project_id)",
        id=conversation_id,
        project_id=project_id,
    )
    for user_id_, member_role in [(owner_id, "owner"), *((m, "member") for m in member_ids)]:
        await execute(
            engine,
            "INSERT INTO project_members (project_id, user_id, member_role, role_id) "
            "VALUES (:project_id, :user_id, :member_role, NULL)",
            project_id=project_id,
            user_id=user_id_,
            member_role=member_role,
        )
        await execute(
            engine,
            "INSERT INTO conversation_members (conversation_id, user_id) VALUES (:cid, :uid)",
            cid=conversation_id,
            uid=user_id_,
        )
    return project_id, conversation_id


async def connect(url: str, origin: str, session: str | None) -> socketio.AsyncClient:
    # WebSocket closes immediately on disconnect; long-polling would hold a request open
    # for the full ping interval (~25 s). Both transports go through the same checks.
    headers = {"Origin": origin}
    if session is not None:
        headers["Cookie"] = f"{SESSION_COOKIE}={session}"
    client = socketio.AsyncClient(reconnection=False)
    await client.connect(url, headers=headers, transports=["websocket"], wait_timeout=5)
    return client
