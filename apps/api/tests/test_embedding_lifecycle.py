"""Profile saves and account deletion drive the embeddings table; the worker never runs here."""

import asyncio
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from alembic import command
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tests.conftest import TEST_DATABASE_URL, MemoryMailer, alembic_config
from tests.helpers import PASSWORD, execute, profile, scalar, student

pytestmark = pytest.mark.usefixtures("clean_database")

ADA = "ada@northeastern.edu"


@pytest.fixture
async def ada(app: object, mailer: MemoryMailer) -> AsyncIterator[httpx.AsyncClient]:
    client = await student(app, mailer, ADA)
    yield client
    await client.aclose()


async def row_count(engine: AsyncEngine, user_id: str) -> int:
    return int(
        str(await scalar(engine, "SELECT count(*) FROM embeddings WHERE user_id = :id", id=user_id))
    )


async def is_stale(engine: AsyncEngine, user_id: str) -> bool:
    sql = "SELECT stale FROM embeddings WHERE user_id = :id"
    return bool(await scalar(engine, sql, id=user_id))


async def updated_at(engine: AsyncEngine, user_id: str) -> object:
    return await scalar(engine, "SELECT updated_at FROM embeddings WHERE user_id = :id", id=user_id)


async def mark_fresh(engine: AsyncEngine, user_id: str) -> None:
    """Fakes what the worker leaves behind once it has encoded a row, so a re-save can be
    proven to queue a fresh encode rather than leaving a stale-but-already-fresh row alone."""
    vector = "[" + ",".join(["0.1"] * 384) + "]"
    await execute(
        engine,
        "UPDATE embeddings SET stale = false, model_version = 'fake-v1',"
        " source_hash = repeat('a', 64), embedding = :vector WHERE user_id = :id",
        vector=vector,
        id=user_id,
    )


# --- profile save ---------------------------------------------------------------------------


async def test_a_discoverable_save_creates_one_stale_row(
    ada: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    saved = await ada.put("/api/v1/me/profile", json=profile())
    assert saved.status_code == 200, saved.text
    user_id = saved.json()["user_id"]

    assert await row_count(clean_database, user_id) == 1
    assert await is_stale(clean_database, user_id) is True


async def test_saving_again_keeps_one_row_stale_with_updated_at_advanced(
    ada: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    first = await ada.put("/api/v1/me/profile", json=profile())
    user_id = first.json()["user_id"]
    # Simulate the worker having already encoded it, so re-saving must mark it stale again
    # rather than leaving an unrelated "already fresh" row untouched.
    await mark_fresh(clean_database, user_id)
    first_updated = await updated_at(clean_database, user_id)

    second = await ada.put("/api/v1/me/profile", json=profile(headline="A different headline"))
    assert second.status_code == 200

    assert await row_count(clean_database, user_id) == 1
    assert await is_stale(clean_database, user_id) is True
    assert await updated_at(clean_database, user_id) > first_updated  # type: ignore[operator]


async def test_turning_discovery_off_deletes_the_row_in_the_same_request(
    ada: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    saved = await ada.put("/api/v1/me/profile", json=profile())
    user_id = saved.json()["user_id"]
    assert await row_count(clean_database, user_id) == 1

    off = await ada.put("/api/v1/me/profile", json=profile(discoverable=False))
    assert off.status_code == 200, off.text
    assert await row_count(clean_database, user_id) == 0


async def test_opting_back_in_queues_a_fresh_stale_row(
    ada: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    hidden = await ada.put("/api/v1/me/profile", json=profile(discoverable=False))
    user_id = hidden.json()["user_id"]
    assert await row_count(clean_database, user_id) == 0

    back_on = await ada.put("/api/v1/me/profile", json=profile(discoverable=True))
    assert back_on.status_code == 200, back_on.text
    assert await row_count(clean_database, user_id) == 1
    assert await is_stale(clean_database, user_id) is True


async def test_a_never_discoverable_student_never_gets_a_row(
    ada: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    saved = await ada.put("/api/v1/me/profile", json=profile(discoverable=False))
    assert saved.status_code == 200, saved.text

    assert await row_count(clean_database, saved.json()["user_id"]) == 0


async def test_a_mid_save_database_failure_leaves_nothing_committed(
    ada: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    """Breaking the embeddings write partway through a save must roll back the profile write
    too, which is only true if both happen in the one transaction."""
    await execute(clean_database, "ALTER TABLE embeddings RENAME TO embeddings_hidden")
    try:
        response = await ada.put("/api/v1/me/profile", json=profile())
        assert response.status_code == 500
    finally:
        await execute(clean_database, "ALTER TABLE embeddings_hidden RENAME TO embeddings")

    me = await ada.get("/api/v1/me")
    user_id = me.json()["id"]
    sql = "SELECT count(*) FROM profiles WHERE user_id = :id"
    assert await scalar(clean_database, sql, id=user_id) == 0


async def test_a_save_that_fails_validation_leaves_the_embedding_row_untouched(
    ada: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    saved = await ada.put("/api/v1/me/profile", json=profile())
    user_id = saved.json()["user_id"]
    before = await updated_at(clean_database, user_id)

    invalid = await ada.put(
        "/api/v1/me/profile",
        json=profile(skills=[{"slug": "basket-weaving", "level": "advanced"}]),
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "unknown_skill"

    assert await row_count(clean_database, user_id) == 1
    assert await updated_at(clean_database, user_id) == before


# --- account deletion ------------------------------------------------------------------------


async def test_deleting_the_account_leaves_no_embedding_row(
    ada: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    saved = await ada.put("/api/v1/me/profile", json=profile())
    user_id = saved.json()["user_id"]
    assert await row_count(clean_database, user_id) == 1

    response = await ada.request("DELETE", "/api/v1/me", json={"password": PASSWORD})
    assert response.status_code == 204

    assert await row_count(clean_database, user_id) == 0


# --- migration backfill ----------------------------------------------------------------------


async def _seed_profiles(discoverable_id: str, hidden_id: str) -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        for user_id, discoverable in ((discoverable_id, True), (hidden_id, False)):
            await execute(
                engine,
                "INSERT INTO users (id, email, password_hash) VALUES (:id, :email, 'x')",
                id=user_id,
                email=f"{user_id}@northeastern.edu",
            )
            await execute(
                engine,
                "INSERT INTO profiles (user_id, display_name, timezone, discoverable)"
                " VALUES (:id, 'Name', 'America/New_York', :discoverable)",
                id=user_id,
                discoverable=discoverable,
            )
    finally:
        await engine.dispose()


async def _stale_row_counts(user_ids: list[str]) -> dict[str, int]:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        return {
            user_id: int(
                str(
                    await scalar(
                        engine,
                        "SELECT count(*) FROM embeddings WHERE user_id = :id AND stale",
                        id=user_id,
                    )
                )
            )
            for user_id in user_ids
        }
    finally:
        await engine.dispose()


@pytest.mark.usefixtures("migrated")
def test_migration_backfills_a_stale_row_for_each_discoverable_profile_only() -> None:
    config = alembic_config()
    discoverable_id, hidden_id = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        command.downgrade(config, "0009")
        asyncio.run(_seed_profiles(discoverable_id, hidden_id))
        command.upgrade(config, "head")

        assert asyncio.run(_stale_row_counts([discoverable_id, hidden_id])) == {
            discoverable_id: 1,
            hidden_id: 0,
        }
    finally:
        command.upgrade(config, "head")
