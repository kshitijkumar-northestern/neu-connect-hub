from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


@dataclass(frozen=True)
class Database:
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]


def create_database(settings: Settings) -> Database:
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        # asyncpg waits 60 seconds by default; fail fast so readiness checks stay responsive.
        connect_args={"timeout": settings.db_connect_timeout_seconds},
    )
    return Database(engine=engine, sessionmaker=async_sessionmaker(engine, expire_on_commit=False))


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request."""
    database: Database = request.app.state.db
    async with database.sessionmaker() as session:
        yield session
