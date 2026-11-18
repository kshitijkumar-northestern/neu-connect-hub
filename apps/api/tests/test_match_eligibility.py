"""Who the ranker may even consider, decided in SQL before any scoring runs."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.modules.auth.models import User
from app.modules.discovery.eligibility import candidate_filter, viewer_eligible
from app.modules.profiles.models import Profile
from tests.conftest import MemoryMailer
from tests.helpers import (
    block,
    execute,
    make_admin,
    student,
    student_with_profile,
    suspend,
    unverify,
    user_id,
)

pytestmark = pytest.mark.usefixtures("clean_database")


async def raw_connection(engine: AsyncEngine, a: str, b: str, state: str) -> None:
    await execute(
        engine,
        "INSERT INTO connections (id, sender_id, recipient_id, state) VALUES (:id, :a, :b, :state)",
        id=str(uuid.uuid4()),
        a=a,
        b=b,
        state=state,
    )


async def pool(session: AsyncSession, viewer_id: str) -> set[str]:
    rows = await session.execute(
        select(Profile.user_id)
        .join(User, User.id == Profile.user_id)
        .where(candidate_filter(uuid.UUID(viewer_id)))
    )
    return {str(row) for row in rows.scalars().all()}


# --- viewer_eligible ------------------------------------------------------------------------


async def test_a_verified_opted_in_student_is_eligible(
    app: object, mailer: MemoryMailer, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    uid = await user_id(ada)
    assert await viewer_eligible(db_session, uuid.UUID(uid)) is True


async def test_an_unverified_viewer_is_not_eligible(
    app: object, mailer: MemoryMailer, engine: AsyncEngine, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    uid = await user_id(ada)
    await unverify(engine, uid)
    assert await viewer_eligible(db_session, uuid.UUID(uid)) is False


async def test_a_suspended_viewer_is_not_eligible(
    app: object, mailer: MemoryMailer, engine: AsyncEngine, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    uid = await user_id(ada)
    await suspend(engine, uid)
    assert await viewer_eligible(db_session, uuid.UUID(uid)) is False


async def test_an_admin_viewer_is_not_eligible(
    app: object, mailer: MemoryMailer, engine: AsyncEngine, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    uid = await user_id(ada)
    await make_admin(engine, uid)
    assert await viewer_eligible(db_session, uuid.UUID(uid)) is False


async def test_a_viewer_with_no_profile_is_not_eligible(
    app: object, mailer: MemoryMailer, db_session: AsyncSession
) -> None:
    ada = await student(app, mailer, "ada@northeastern.edu")
    uid = await user_id(ada)
    assert await viewer_eligible(db_session, uuid.UUID(uid)) is False


async def test_a_viewer_not_opted_into_discovery_is_not_eligible(
    app: object, mailer: MemoryMailer, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu", discoverable=False)
    uid = await user_id(ada)
    assert await viewer_eligible(db_session, uuid.UUID(uid)) is False


# --- candidate_filter: each exclusion on its own -------------------------------------------


async def test_a_discoverable_candidate_with_availability_is_in_the_pool(
    app: object, mailer: MemoryMailer, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    grace = await student_with_profile(app, mailer, "grace@northeastern.edu")
    viewer_id, candidate_id = await user_id(ada), await user_id(grace)
    assert candidate_id in await pool(db_session, viewer_id)


async def test_a_candidate_who_opted_out_of_discovery_is_excluded(
    app: object, mailer: MemoryMailer, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    grace = await student_with_profile(app, mailer, "grace@northeastern.edu", discoverable=False)
    viewer_id, candidate_id = await user_id(ada), await user_id(grace)
    assert candidate_id not in await pool(db_session, viewer_id)


async def test_an_unverified_candidate_is_excluded(
    app: object, mailer: MemoryMailer, engine: AsyncEngine, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    grace = await student_with_profile(app, mailer, "grace@northeastern.edu")
    viewer_id, candidate_id = await user_id(ada), await user_id(grace)
    await unverify(engine, candidate_id)
    assert candidate_id not in await pool(db_session, viewer_id)


async def test_a_suspended_candidate_is_excluded(
    app: object, mailer: MemoryMailer, engine: AsyncEngine, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    grace = await student_with_profile(app, mailer, "grace@northeastern.edu")
    viewer_id, candidate_id = await user_id(ada), await user_id(grace)
    await suspend(engine, candidate_id)
    assert candidate_id not in await pool(db_session, viewer_id)


async def test_an_admin_candidate_is_excluded(
    app: object, mailer: MemoryMailer, engine: AsyncEngine, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    grace = await student_with_profile(app, mailer, "grace@northeastern.edu")
    viewer_id, candidate_id = await user_id(ada), await user_id(grace)
    await make_admin(engine, candidate_id)
    assert candidate_id not in await pool(db_session, viewer_id)


async def test_a_candidate_blocked_by_the_viewer_is_excluded(
    app: object, mailer: MemoryMailer, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    grace = await student_with_profile(app, mailer, "grace@northeastern.edu")
    viewer_id, candidate_id = await user_id(ada), await user_id(grace)
    await block(ada, candidate_id)
    assert candidate_id not in await pool(db_session, viewer_id)


async def test_a_candidate_who_blocked_the_viewer_is_excluded(
    app: object, mailer: MemoryMailer, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    grace = await student_with_profile(app, mailer, "grace@northeastern.edu")
    viewer_id, candidate_id = await user_id(ada), await user_id(grace)
    await block(grace, viewer_id)
    assert candidate_id not in await pool(db_session, viewer_id)


@pytest.mark.parametrize("direction", ["viewer_to_candidate", "candidate_to_viewer"])
async def test_a_pending_connection_either_direction_excludes(
    app: object, mailer: MemoryMailer, db_session: AsyncSession, direction: str
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    grace = await student_with_profile(app, mailer, "grace@northeastern.edu")
    viewer_id, candidate_id = await user_id(ada), await user_id(grace)
    sender, target_id = (
        (ada, candidate_id) if direction == "viewer_to_candidate" else (grace, viewer_id)
    )
    response = await sender.post("/api/v1/connections", json={"user_id": target_id})
    assert response.status_code == 201, response.text
    assert candidate_id not in await pool(db_session, viewer_id)


async def test_an_accepted_connection_excludes(
    app: object, mailer: MemoryMailer, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    grace = await student_with_profile(app, mailer, "grace@northeastern.edu")
    viewer_id, candidate_id = await user_id(ada), await user_id(grace)
    sent = (await ada.post("/api/v1/connections", json={"user_id": candidate_id})).json()
    accepted = await grace.post(f"/api/v1/connections/{sent['id']}/accept")
    assert accepted.status_code == 200, accepted.text
    assert candidate_id not in await pool(db_session, viewer_id)


async def test_a_candidate_with_no_availability_is_excluded(
    app: object, mailer: MemoryMailer, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    grace = await student_with_profile(app, mailer, "grace@northeastern.edu", availability=[])
    viewer_id, candidate_id = await user_id(ada), await user_id(grace)
    assert candidate_id not in await pool(db_session, viewer_id)


async def test_the_viewer_never_recommends_themselves(
    app: object, mailer: MemoryMailer, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    viewer_id = await user_id(ada)
    assert viewer_id not in await pool(db_session, viewer_id)


# --- closed connections never exclude -------------------------------------------------------


@pytest.mark.parametrize("state", ["declined", "withdrawn", "ended"])
async def test_a_closed_connection_does_not_exclude(
    app: object, mailer: MemoryMailer, engine: AsyncEngine, db_session: AsyncSession, state: str
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    grace = await student_with_profile(app, mailer, "grace@northeastern.edu")
    viewer_id, candidate_id = await user_id(ada), await user_id(grace)
    await raw_connection(engine, viewer_id, candidate_id, state)
    assert candidate_id in await pool(db_session, viewer_id)
