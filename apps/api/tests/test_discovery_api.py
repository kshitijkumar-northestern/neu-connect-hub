"""The discovery HTTP API: eligibility, shape, the project override, rate limiting, and that a
candidate who becomes invisible between the ranker and the card query is dropped."""

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ml.scoring import Components, SharedGoals
from app.modules.discovery import service as discovery_service
from app.modules.discovery.ranker import Recommendation
from app.modules.discovery.service import DISCOVERY_PER_USER
from tests.conftest import MemoryMailer
from tests.helpers import (
    make_admin,
    new_client,
    profile,
    student,
    student_with_profile,
    user_id,
)

pytestmark = pytest.mark.usefixtures("clean_database")

ADA = "ada@northeastern.edu"
GRACE = "grace@northeastern.edu"
HENRY = "henry@northeastern.edu"
ISABEL = "isabel@northeastern.edu"
JAMAL = "jamal@northeastern.edu"

DISCOVERY_URL = "/api/v1/discovery"

MATCH_KEYS = {"user_id", "display_name", "headline", "reasons"}
REASON_KEYS_BY_KIND = {
    "skills_covered": {"kind", "items"},
    "shared_interests": {"kind", "items"},
    "availability_overlap": {"kind", "window"},
    "shared_goals": {"kind", "goals"},
}


async def test_anonymous_request_is_rejected(app: object) -> None:
    async with new_client(app) as anonymous:
        response = await anonymous.get(DISCOVERY_URL)
    assert response.status_code == 401


async def test_unverified_student_is_rejected(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student(app, mailer, ADA, verified=False)
    response = await ada.get(DISCOVERY_URL)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "verification_required"


async def test_a_viewer_not_opted_in_gets_the_opt_in_error(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student(app, mailer, ADA)
    await ada.put("/api/v1/me/profile", json=profile(discoverable=False))

    response = await ada.get(DISCOVERY_URL)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "discovery_opt_in_required"


async def test_at_most_five_results_with_a_stable_shape(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    for i in range(7):
        await student_with_profile(app, mailer, f"candidate{i}@northeastern.edu")

    response = await ada.get(DISCOVERY_URL)
    assert response.status_code == 200
    matches = response.json()["matches"]
    assert len(matches) == 5
    for match in matches:
        assert set(match.keys()) == MATCH_KEYS
        assert len(match["reasons"]) >= 1
        for reason in match["reasons"]:
            assert set(reason.keys()) == REASON_KEYS_BY_KIND[reason["kind"]]


async def test_hidden_and_blocked_candidates_are_absent(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    grace = await student_with_profile(app, mailer, GRACE, discoverable=False)
    henry = await student_with_profile(app, mailer, HENRY)
    eligible = await student_with_profile(app, mailer, "eligible@northeastern.edu")

    ada_id = await user_id(ada)
    henry_id = await user_id(henry)
    eligible_id = await user_id(eligible)
    await ada.post("/api/v1/blocks", json={"user_id": henry_id})

    response = await ada.get(DISCOVERY_URL)
    assert response.status_code == 200
    ids = {m["user_id"] for m in response.json()["matches"]}
    assert ids == {eligible_id}
    assert await user_id(grace) not in ids
    assert ada_id not in ids


async def test_discovery_is_rate_limited(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    for _ in range(DISCOVERY_PER_USER.max_events):
        assert (await ada.get(DISCOVERY_URL)).status_code == 200
    limited = await ada.get(DISCOVERY_URL)
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers


async def test_a_candidate_blocked_between_the_ranker_and_the_card_query_is_dropped(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    grace = await student_with_profile(app, mailer, GRACE)
    grace_id = await user_id(grace)
    await ada.post("/api/v1/blocks", json={"user_id": grace_id})

    fake = Recommendation(
        user_id=uuid.UUID(grace_id),
        score=1.0,
        components=Components(semantic=0, complementary=0, availability=0, goal=1),
        reasons=(SharedGoals(goals=("research",)),),
        fallback=None,
    )

    async def fake_recommend(self: object, viewer_id: object, **kwargs: object) -> list[object]:
        return [fake]

    monkeypatch.setattr(discovery_service.Ranker, "recommend", fake_recommend)

    response = await ada.get(DISCOVERY_URL)
    assert response.status_code == 200
    assert response.json()["matches"] == []


# --- the project override -------------------------------------------------------------------


async def create_project(
    client: httpx.AsyncClient,
    *,
    capacity: int = 10,
    roles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    body = {
        "title": "Music generator",
        "summary": "",
        "capacity": capacity,
        "roles": roles
        if roles is not None
        else [{"title": "Engineer", "open_slots": 5, "skills": ["python", "figma"]}],
    }
    created = await client.post("/api/v1/projects", json=body)
    assert created.status_code == 201, created.text
    return dict(created.json())


async def make_project(client: httpx.AsyncClient) -> str:
    return str((await create_project(client))["id"])


async def test_a_non_owner_gets_404_for_the_project_override(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    grace = await student_with_profile(app, mailer, GRACE)
    project_id = await make_project(ada)

    response = await grace.get(DISCOVERY_URL, params={"project_id": project_id})
    assert response.status_code == 404


async def test_an_unknown_project_gets_404(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    response = await ada.get(
        DISCOVERY_URL, params={"project_id": "00000000-0000-0000-0000-000000000000"}
    )
    assert response.status_code == 404


async def test_a_closed_project_gets_404_even_for_the_owner(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    project_id = await make_project(ada)
    await ada.patch(f"/api/v1/projects/{project_id}", json={"status": "closed"})

    response = await ada.get(DISCOVERY_URL, params={"project_id": project_id})
    assert response.status_code == 404


async def test_project_override_favours_missing_skills_and_excludes_members_and_applicants(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    # Ada already has python; the role also needs figma, which nobody on the team has yet.
    ada = await student_with_profile(app, mailer, ADA)
    project_id = await make_project(ada)

    grace = await student_with_profile(
        app, mailer, GRACE, skills=[{"slug": "figma", "level": "intermediate"}]
    )
    henry = await student_with_profile(
        app, mailer, HENRY, skills=[{"slug": "python", "level": "advanced"}]
    )
    isabel = await student_with_profile(
        app, mailer, ISABEL, skills=[{"slug": "python", "level": "advanced"}]
    )
    jamal = await student_with_profile(
        app, mailer, JAMAL, skills=[{"slug": "figma", "level": "beginner"}]
    )
    grace_id, henry_id = await user_id(grace), await user_id(henry)
    isabel_id, jamal_id = await user_id(isabel), await user_id(jamal)

    # Isabel joins the team (so her skills count against "missing", and she must be excluded).
    applied = (await isabel.post(f"/api/v1/projects/{project_id}/apply", json={})).json()
    await ada.post(f"/api/v1/projects/{project_id}/applications/{applied['id']}/accept")
    # Jamal has a pending application: a perfect skill match, but still excluded.
    await jamal.post(f"/api/v1/projects/{project_id}/apply", json={})

    response = await ada.get(DISCOVERY_URL, params={"project_id": project_id})
    assert response.status_code == 200
    ids = [m["user_id"] for m in response.json()["matches"]]

    assert isabel_id not in ids
    assert jamal_id not in ids
    assert grace_id in ids and henry_id in ids
    # Grace covers the missing skill (figma); Henry doesn't, so Grace outranks him.
    assert ids.index(grace_id) < ids.index(henry_id)


async def test_a_full_project_gives_404(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    grace = await student_with_profile(app, mailer, GRACE)
    created = await create_project(ada, capacity=2)
    project_id = created["id"]

    applied = (await grace.post(f"/api/v1/projects/{project_id}/apply", json={})).json()
    accept = await ada.post(f"/api/v1/projects/{project_id}/applications/{applied['id']}/accept")
    assert accept.status_code == 200, accept.text
    assert accept.json()  # sanity: the accept actually went through

    response = await ada.get(DISCOVERY_URL, params={"project_id": project_id})
    assert response.status_code == 404


async def test_project_override_only_favours_skills_from_roles_with_room(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    created = await create_project(
        ada,
        roles=[
            {"title": "Filled", "open_slots": 1, "skills": ["data-analysis"]},
            {"title": "Open", "open_slots": 5, "skills": ["cybersecurity"]},
        ],
    )
    project_id = created["id"]
    filled_role_id = next(r["id"] for r in created["roles"] if r["title"] == "Filled")

    filler = await student_with_profile(app, mailer, "filler@northeastern.edu")
    applied = (
        await filler.post(f"/api/v1/projects/{project_id}/apply", json={"role_id": filled_role_id})
    ).json()
    accept = await ada.post(f"/api/v1/projects/{project_id}/applications/{applied['id']}/accept")
    assert accept.status_code == 200, accept.text

    # Keith covers the still-open role's skill; Laura only covers the already-filled role's
    # skill, which must not count toward "missing" any more.
    keith = await student_with_profile(
        app,
        mailer,
        "keith@northeastern.edu",
        skills=[{"slug": "cybersecurity", "level": "advanced"}],
    )
    laura = await student_with_profile(
        app,
        mailer,
        "laura@northeastern.edu",
        skills=[{"slug": "data-analysis", "level": "advanced"}],
    )
    keith_id, laura_id = await user_id(keith), await user_id(laura)

    response = await ada.get(DISCOVERY_URL, params={"project_id": project_id})
    assert response.status_code == 200
    ids = [m["user_id"] for m in response.json()["matches"]]
    assert keith_id in ids and laura_id in ids
    assert ids.index(keith_id) < ids.index(laura_id)


async def test_an_admin_gets_the_students_only_error(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    ada_id = await user_id(ada)
    await make_admin(clean_database, ada_id)

    response = await ada.get(DISCOVERY_URL)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "discovery_students_only"
