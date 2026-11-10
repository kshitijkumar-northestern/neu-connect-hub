import asyncio

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import TEST_DATABASE_URL, alembic_config


def vector_installed() -> bool:
    async def query() -> bool:
        engine = create_async_engine(TEST_DATABASE_URL)
        try:
            async with engine.connect() as connection:
                found = await connection.scalar(
                    text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
                )
        finally:
            await engine.dispose()
        return bool(found)

    return asyncio.run(query())


@pytest.mark.usefixtures("migrated")
def test_migrations_downgrade_and_upgrade_cleanly() -> None:
    config = alembic_config()

    command.downgrade(config, "base")
    assert vector_installed() is False

    command.upgrade(config, "head")
    assert vector_installed() is True


@pytest.mark.usefixtures("migrated")
def test_models_and_migrations_have_no_drift() -> None:
    command.check(alembic_config())
