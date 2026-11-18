from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.modules.profiles.schemas import ProfileInput
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


async def test_seeded_profiles_validate_and_have_no_overlapping_availability(
    settings: Settings, clean_database: AsyncEngine
) -> None:
    """Verify that all seeded students' profiles pass ProfileInput validation and that
    no two availability slots overlap on the same weekday."""
    await seed(settings, count=40, password=DEMO_PASSWORD)

    # Query all profiles, skills, interests, and availability slots
    async with clean_database.begin() as connection:
        profiles_result = await connection.execute(
            text(
                "SELECT user_id, display_name, headline, bio, goals, timezone,"
                " discoverable, feedback_consent FROM profiles ORDER BY user_id"
            )
        )
        profiles = profiles_result.all()

        # Map skill_id to slug for each student
        skills_result = await connection.execute(
            text(
                "SELECT ps.user_id, s.slug, ps.kind, ps.proficiency FROM profile_skills ps "
                "JOIN skills s ON s.id = ps.skill_id ORDER BY ps.user_id, s.slug"
            )
        )
        skills_by_user: dict[Any, dict[str, list[Any]]] = {}
        for user_id, slug, kind, proficiency in skills_result:
            if user_id not in skills_by_user:
                skills_by_user[user_id] = {"has": [], "seeking": []}
            if kind == "has":
                level = ["beginner", "intermediate", "advanced"][proficiency - 1]
                skills_by_user[user_id]["has"].append({"slug": slug, "level": level})
            else:
                skills_by_user[user_id]["seeking"].append(slug)

        # Map interest_id to slug for each student
        interests_result = await connection.execute(
            text(
                "SELECT pi.user_id, i.slug FROM profile_interests pi "
                "JOIN interests i ON i.id = pi.interest_id ORDER BY pi.user_id, i.slug"
            )
        )
        interests_by_user: dict[Any, list[Any]] = {}
        for user_id, slug in interests_result:
            if user_id not in interests_by_user:
                interests_by_user[user_id] = []
            interests_by_user[user_id].append(slug)

        # Get availability slots for each student
        slots_result = await connection.execute(
            text(
                "SELECT user_id, weekday, start_min, end_min FROM availability_slots"
                " ORDER BY user_id, weekday, start_min"
            )
        )
        slots_by_user: dict[Any, list[dict[str, Any]]] = {}
        for user_id, weekday, start_min, end_min in slots_result:
            if user_id not in slots_by_user:
                slots_by_user[user_id] = []
            slots_by_user[user_id].append(
                {"weekday": weekday, "start_min": start_min, "end_min": end_min}
            )

    # Validate each profile
    for profile_row in profiles:
        (
            user_id,
            display_name,
            headline,
            bio,
            goals,
            timezone,
            discoverable,
            feedback_consent,
        ) = profile_row

        # Check for overlapping slots on the same weekday
        slots = slots_by_user.get(user_id, [])
        for i, slot1 in enumerate(slots):
            for slot2 in slots[i + 1 :]:
                if slot1["weekday"] == slot2["weekday"]:
                    overlap = (
                        slot1["start_min"] < slot2["end_min"]
                        and slot2["start_min"] < slot1["end_min"]
                    )
                    if overlap:
                        pytest.fail(
                            f"User {user_id} has overlapping slots on"
                            f" weekday {slot1['weekday']}: "
                            f"{slot1['start_min']}-{slot1['end_min']} and "
                            f"{slot2['start_min']}-{slot2['end_min']}"
                        )

        # Build ProfileInput dict
        profile_input = ProfileInput(
            display_name=display_name,
            headline=headline or "",
            bio=bio or "",
            goals=list(goals) if goals else [],
            timezone=timezone,
            skills=skills_by_user.get(user_id, {}).get("has", []),
            seeking_skills=skills_by_user.get(user_id, {}).get("seeking", []),
            interests=interests_by_user.get(user_id, []),
            availability=slots,
            discoverable=discoverable,
            feedback_consent=feedback_consent,
        )

        # If we got here, validation passed (ProfileInput constructor validates on construction)
        assert profile_input is not None


async def test_discoverable_demo_students_are_queued_for_embedding(
    settings: Settings, clean_database: AsyncEngine
) -> None:
    """Seeding bypasses the profile API, so it must queue embeddings itself, as a profile save
    would: one stale row per discoverable student and none for the rest."""
    await seed(settings, count=12, password=DEMO_PASSWORD)

    async with clean_database.connect() as connection:
        queued = (
            await connection.execute(
                text("SELECT user_id, stale, embedding IS NULL FROM embeddings ORDER BY user_id")
            )
        ).all()
        discoverable = (
            await connection.execute(
                text("SELECT user_id FROM profiles WHERE discoverable ORDER BY user_id")
            )
        ).all()

    assert [row[0] for row in queued] == [row[0] for row in discoverable]
    assert all(stale and empty for _, stale, empty in queued)
