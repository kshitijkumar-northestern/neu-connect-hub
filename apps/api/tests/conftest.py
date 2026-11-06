import asyncio
import os
import re
import socket
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://app:local@localhost:5432/nu_hub_test"
)
TEST_SESSION_SECRET = "test-session-secret-that-is-long-enough"
WEB_ORIGIN = "http://localhost:8080"

# Assigned, not defaulted: tests must never reach the development database, even if a
# developer's shell exports DATABASE_URL or apps/api/.env points at nu_hub.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["SESSION_SECRET"] = TEST_SESSION_SECRET
os.environ["APP_ENV"] = "test"

from app.core.config import Settings  # noqa: E402
from app.core.mail import EmailMessage  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.main import create_app  # noqa: E402

API_ROOT = Path(__file__).resolve().parents[1]


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_url": TEST_DATABASE_URL,
        "session_secret": TEST_SESSION_SECRET,
        "web_origin": WEB_ORIGIN,
        "allowed_email_domains": "northeastern.edu",
        # Cheap hashing keeps the suite fast; production uses the defaults.
        "argon2_time_cost": 1,
        "argon2_memory_cost_kib": 1024,
        "argon2_parallelism": 1,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


PRODUCTION_SECRET = "a-production-secret-with-more-than-32-characters"


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "production",
        "session_secret": PRODUCTION_SECRET,
        "web_origin": "https://hub.example",
        "mail_backend": "smtp",
        "smtp_host": "smtp.example.com",
    }
    values.update(overrides)
    return make_settings(**values)


class MemoryMailer:
    """Captures outgoing email so tests can follow the links inside."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)

    def token_from_last(self, path: str) -> str:
        """The token in the most recent link to `path`, e.g. "/verify-email"."""
        for message in reversed(self.sent):
            match = re.search(rf"{re.escape(path)}#token=([A-Za-z0-9_-]+)", message.text)
            if match:
                return match.group(1)
        raise AssertionError(f"No email with a {path} link was sent")


def alembic_config() -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    return config


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture(scope="session")
def migrated() -> None:
    """Bring the test database to the latest migration once per run."""
    command.upgrade(alembic_config(), "head")


@pytest.fixture
async def engine(migrated: None) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(TEST_DATABASE_URL)
    yield engine
    await engine.dispose()


@asynccontextmanager
async def rolled_back_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session whose work, including commits and DDL, is rolled back on exit."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with rolled_back_session(engine) as session:
        yield session


@pytest.fixture
async def clean_database(engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """For tests whose writes go through the app's own connections: empty every table after."""
    yield engine
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    if tables:
        async with engine.begin() as connection:
            await connection.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
def mailer() -> MemoryMailer:
    return MemoryMailer()


@pytest.fixture
async def client(settings: Settings, mailer: MemoryMailer) -> AsyncIterator[httpx.AsyncClient]:
    """Talks to the app the way the web client does: same origin, cookies kept."""
    transport = httpx.ASGITransport(app=create_app(settings, mailer=mailer))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"Origin": WEB_ORIGIN}
    ) as client:
        yield client


@pytest.fixture
def free_port() -> Iterator[socket.socket]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    yield sock
    sock.close()


@pytest.fixture
async def live_server(
    settings: Settings, mailer: MemoryMailer, free_port: socket.socket
) -> AsyncIterator[str]:
    """The full ASGI app (HTTP + Socket.IO) served by Uvicorn on a random local port."""
    app = create_app(settings, mailer=mailer)
    config = uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=2)
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(sockets=[free_port]))
    async with asyncio.timeout(10):
        while not server.started:  # noqa: ASYNC110 - Uvicorn exposes a flag, not an event
            await asyncio.sleep(0.01)
    host, port = free_port.getsockname()
    yield f"http://{host}:{port}"
    server.should_exit = True
    await task
