from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.conftest import rolled_back_session


async def test_the_vector_extension_is_installed(db_session: AsyncSession) -> None:
    version = await db_session.scalar(
        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    )

    assert version is not None


async def test_rolled_back_sessions_leave_nothing_behind(engine: AsyncEngine) -> None:
    async with rolled_back_session(engine) as session:
        await session.execute(text("CREATE TABLE probe_rollback (id int PRIMARY KEY)"))
        await session.execute(text("INSERT INTO probe_rollback VALUES (1)"))
        # The commit only releases a savepoint; the outer transaction still rolls back.
        await session.commit()

    async with engine.connect() as connection:
        exists = await connection.scalar(text("SELECT to_regclass('public.probe_rollback')"))

    assert exists is None
