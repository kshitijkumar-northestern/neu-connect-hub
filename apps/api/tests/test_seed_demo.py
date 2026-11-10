import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from scripts.seed_demo import DEMO_DOMAIN, seed
from tests.conftest import production_settings
from tests.helpers import login, new_client

pytestmark = pytest.mark.usefixtures("clean_database")

DEMO_PASSWORD = "a demo passphrase for tests"

SNAPSHOT_QUERIES = [
    "SELECT id, email, role, verified_at IS NOT NULL, suspended_at FROM users ORDER BY email",
    "SELECT user_id, display_name, headline, bio, goals, timezone, discoverable, feedback_consent"
    " FROM profiles ORDER BY user_id",
    "SELECT ps.user_id, s.slug, ps.kind, ps.proficiency FROM profile_skills ps"
    " JOIN skills s ON s.id = ps.skill_id ORDER BY 1, 2, 3",
    "SELECT pi.user_id, i.slug FROM profile_interests pi"
    " JOIN interests i ON i.id = pi.interest_id ORDER BY 1, 2",
    "SELECT user_id, weekday, start_min, end_min FROM availability_slots ORDER BY 1, 2, 3",
]


async def snapshot(engine: AsyncEngine) -> list[list[tuple[object, ...]]]:
    async with engine.connect() as connection:
        return [
            [tuple(row) for row in (await connection.execute(text(sql))).all()]
            for sql in SNAPSHOT_QUERIES
        ]


async def test_seeding_is_deterministic_and_repeatable(
    settings: Settings, clean_database: AsyncEngine
) -> None:
    await seed(settings, count=12, password=DEMO_PASSWORD)
    first = await snapshot(clean_database)
    await seed(settings, count=12, password=DEMO_PASSWORD)

    assert await snapshot(clean_database) == first
    users, profiles, skills, interests, slots = first
    assert len(users) == len(profiles) == 12
    assert skills and interests and slots


async def test_demo_students_are_synthetic_verified_and_mixed(
    settings: Settings, clean_database: AsyncEngine
) -> None:
    await seed(settings, count=12, password=DEMO_PASSWORD)
    users, profiles, *_ = await snapshot(clean_database)

    for _, email, role, verified, suspended_at in users:
        assert str(email).endswith(f"@{DEMO_DOMAIN}")
        assert (role, verified, suspended_at) == ("student", True, None)
    discoverable = {row[6] for row in profiles}
    assert discoverable == {True, False}  # some opt in, some don't


async def test_seeding_leaves_real_accounts_alone(
    settings: Settings, clean_database: AsyncEngine
) -> None:
    async with clean_database.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users (id, email, password_hash) VALUES (gen_random_uuid(), :e, 'x')"
            ),
            {"e": "real@northeastern.edu"},
        )
    await seed(settings, count=3, password=DEMO_PASSWORD)
    await seed(settings, count=3, password=DEMO_PASSWORD)
    async with clean_database.connect() as connection:
        emails = (await connection.execute(text("SELECT email FROM users"))).scalars().all()
    assert "real@northeastern.edu" in emails
    assert len(emails) == 4


async def test_demo_students_can_sign_in(app: object, settings: Settings) -> None:
    await seed(settings, count=2, password=DEMO_PASSWORD)
    async with new_client(app) as client:
        response = await login(client, f"student01@{DEMO_DOMAIN}", DEMO_PASSWORD)
        assert response.status_code == 200
        assert (await client.get("/api/v1/me/profile")).status_code == 200


async def test_seeding_refuses_production() -> None:
    with pytest.raises(SystemExit, match="production"):
        await seed(production_settings(), count=2, password=DEMO_PASSWORD)
