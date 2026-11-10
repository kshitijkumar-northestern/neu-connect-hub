import asyncio
import copy
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import MemoryMailer
from tests.helpers import PROFILE, execute, new_client, student, user_id

pytestmark = pytest.mark.usefixtures("clean_database")

ADA = "ada@northeastern.edu"
GRACE = "grace@northeastern.edu"

PUBLIC_FIELDS = {
    "user_id",
    "display_name",
    "headline",
    "bio",
    "goals",
    "timezone",
    "skills",
    "seeking_skills",
    "interests",
    "availability",
}


def profile(**changes: Any) -> dict[str, Any]:
    body = copy.deepcopy(PROFILE)
    body.update(changes)
    return body


def without_request_id(response: httpx.Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    body["error"].pop("request_id", None)
    return body


@pytest.fixture
async def ada(app: object, mailer: MemoryMailer) -> AsyncIterator[httpx.AsyncClient]:
    client = await student(app, mailer, ADA)
    yield client
    await client.aclose()


@pytest.fixture
async def grace(app: object, mailer: MemoryMailer) -> AsyncIterator[httpx.AsyncClient]:
    client = await student(app, mailer, GRACE)
    yield client
    await client.aclose()


# --- the owner's own profile ----------------------------------------------------------------


async def test_a_new_account_has_no_profile_yet(ada: httpx.AsyncClient) -> None:
    response = await ada.get("/api/v1/me/profile")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "profile_not_found"


async def test_saving_a_profile_returns_it_normalised(ada: httpx.AsyncClient) -> None:
    saved = await ada.put("/api/v1/me/profile", json=profile(display_name="  Ada Lovelace  "))
    assert saved.status_code == 200, saved.text
    body = saved.json()

    assert body["user_id"] == await user_id(ada)
    assert body["display_name"] == "Ada Lovelace"
    assert body["goals"] == ["project_development", "research"]  # the fixed order
    assert body["skills"] == [  # by name
        {"slug": "machine-learning", "name": "Machine learning", "level": "intermediate"},
        {"slug": "python", "name": "Python", "level": "advanced"},
    ]
    assert body["seeking_skills"] == [{"slug": "ui-design", "name": "UI design"}]
    assert body["interests"] == [
        {"slug": "education", "name": "Education"},
        {"slug": "music-and-arts", "name": "Music and arts"},
    ]
    assert body["availability"] == [  # by weekday, then start
        {"weekday": 0, "start_min": 1080, "end_min": 1260},
        {"weekday": 2, "start_min": 600, "end_min": 720},
    ]
    assert body["discoverable"] is True
    assert body["feedback_consent"] is False
    assert (await ada.get("/api/v1/me/profile")).json() == body


async def test_saving_again_replaces_the_whole_profile(ada: httpx.AsyncClient) -> None:
    await ada.put("/api/v1/me/profile", json=PROFILE)
    replaced = await ada.put(
        "/api/v1/me/profile",
        json=profile(skills=[], seeking_skills=[], interests=[], availability=[], goals=[]),
    )
    assert replaced.status_code == 200
    body = replaced.json()
    assert [body[key] for key in ("skills", "seeking_skills", "interests", "availability")] == [
        [],
        [],
        [],
        [],
    ]


async def test_discovery_is_off_unless_the_student_opts_in(ada: httpx.AsyncClient) -> None:
    body = profile()
    del body["discoverable"], body["feedback_consent"]
    saved = (await ada.put("/api/v1/me/profile", json=body)).json()
    assert saved["discoverable"] is False
    assert saved["feedback_consent"] is False


async def test_only_the_owner_can_change_a_profile(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    original = (await ada.put("/api/v1/me/profile", json=PROFILE)).json()
    ada_id = original["user_id"]

    # Another student's /me/profile is their own, and naming a user in the body is refused.
    await grace.put("/api/v1/me/profile", json=profile(display_name="Grace Hopper"))
    assert (await grace.put("/api/v1/me/profile", json=profile(user_id=ada_id))).status_code == 422
    for method in ("PUT", "PATCH", "DELETE"):
        response = await grace.request(method, f"/api/v1/profiles/{ada_id}", json=PROFILE)
        assert response.status_code == 405, method

    assert (await ada.get("/api/v1/me/profile")).json() == original


async def test_concurrent_saves_never_fail_or_mix(ada: httpx.AsyncClient) -> None:
    versions = [
        profile(skills=[{"slug": slug, "level": "beginner"}], interests=[interest])
        for slug, interest in [("go", "gaming"), ("rust", "space"), ("java", "fintech")]
    ]
    responses = await asyncio.gather(
        *(ada.put("/api/v1/me/profile", json=body) for body in versions * 3)
    )
    assert [r.status_code for r in responses] == [200] * 9

    final = (await ada.get("/api/v1/me/profile")).json()
    pairs = {(v["skills"][0]["slug"], v["interests"][0]) for v in versions}
    assert len(final["skills"]) == 1 and len(final["interests"]) == 1
    assert (final["skills"][0]["slug"], final["interests"][0]["slug"]) in pairs


async def test_unverified_students_cannot_have_a_profile(app: object, mailer: MemoryMailer) -> None:
    client = await student(app, mailer, "new@northeastern.edu", verified=False)
    try:
        for method in ("GET", "PUT"):
            response = await client.request(method, "/api/v1/me/profile", json=PROFILE)
            assert response.status_code == 403, method
            assert response.json()["error"]["code"] == "verification_required"
    finally:
        await client.aclose()


async def test_profile_routes_require_a_session(app: object) -> None:
    async with new_client(app) as anonymous:
        for method, path in [
            ("GET", "/api/v1/me/profile"),
            ("PUT", "/api/v1/me/profile"),
            ("GET", f"/api/v1/profiles/{uuid.uuid4()}"),
            ("GET", "/api/v1/skills"),
            ("GET", "/api/v1/interests"),
        ]:
            assert (await anonymous.request(method, path)).status_code == 401, path


# --- validation -----------------------------------------------------------------------------


def _slot(weekday: int, start: int, end: int) -> dict[str, int]:
    return {"weekday": weekday, "start_min": start, "end_min": end}


INVALID: dict[str, dict[str, Any]] = {
    "empty name": {"display_name": "   "},
    "long name": {"display_name": "x" * 81},
    "control character in name": {"display_name": "Ada\u0000"},
    "right-to-left override in name": {"display_name": "Ada \u202ecilA"},
    "bidi isolate in headline": {"headline": "Hi \u2067there"},
    "long headline": {"headline": "x" * 121},
    "newline in headline": {"headline": "one\ntwo"},
    "long bio": {"bio": "x" * 1001},
    "unknown goal": {"goals": ["sleeping"]},
    "repeated goal": {"goals": ["research", "research"]},
    "unknown timezone": {"timezone": "Mars/Olympus_Mons"},
    "empty timezone": {"timezone": ""},
    "unknown level": {"skills": [{"slug": "python", "level": "guru"}]},
    "repeated skill": {"skills": [{"slug": "python", "level": "advanced"}] * 2},
    "repeated seeking skill": {"seeking_skills": ["python", "python"]},
    "repeated interest": {"interests": ["space", "space"]},
    "too many skills": {"skills": [{"slug": f"s{i}", "level": "beginner"} for i in range(21)]},
    "too many interests": {"interests": [f"i{i}" for i in range(16)]},
    "weekday too high": {"availability": [_slot(7, 600, 660)]},
    "weekday negative": {"availability": [_slot(-1, 600, 660)]},
    "start before midnight": {"availability": [_slot(0, -1, 660)]},
    "end after midnight": {"availability": [_slot(0, 600, 1441)]},
    "empty range": {"availability": [_slot(0, 600, 600)]},
    "reversed range": {"availability": [_slot(0, 700, 600)]},
    "overlapping ranges": {"availability": [_slot(1, 600, 720), _slot(1, 700, 800)]},
    "too many ranges": {
        "availability": [_slot(d, m * 30, m * 30 + 15) for d in range(7) for m in range(7)]
    },
    "unknown field": {"email": "someone@northeastern.edu"},
    "non-boolean flag": {"discoverable": "yes please"},
}


@pytest.mark.parametrize("changes", INVALID.values(), ids=INVALID.keys())
async def test_invalid_profiles_are_rejected(
    ada: httpx.AsyncClient, changes: dict[str, Any]
) -> None:
    response = await ada.put("/api/v1/me/profile", json=profile(**changes))
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    assert (await ada.get("/api/v1/me/profile")).status_code == 404  # nothing was saved


async def test_names_may_use_zero_width_joiners(ada: httpx.AsyncClient) -> None:
    # Persian and Indic scripts need ZWNJ and ZWJ to spell names correctly.
    name = "\u0645\u06cc\u200c\u0631\u0648\u062f \u0915\u094d\u200d\u0937"
    response = await ada.put("/api/v1/me/profile", json=profile(display_name=name))
    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == name


async def test_adjacent_ranges_on_one_day_are_fine(ada: httpx.AsyncClient) -> None:
    slots = [_slot(4, 600, 720), _slot(4, 720, 1440)]
    response = await ada.put("/api/v1/me/profile", json=profile(availability=slots))
    assert response.status_code == 200
    assert response.json()["availability"] == slots


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"skills": [{"slug": "basket-weaving", "level": "advanced"}]}, "unknown_skill"),
        ({"seeking_skills": ["basket-weaving"]}, "unknown_skill"),
        ({"interests": ["competitive-napping"]}, "unknown_interest"),
    ],
    ids=["skill", "seeking skill", "interest"],
)
async def test_skills_and_interests_must_come_from_the_lists(
    ada: httpx.AsyncClient, changes: dict[str, Any], code: str
) -> None:
    response = await ada.put("/api/v1/me/profile", json=profile(**changes))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert (await ada.get("/api/v1/me/profile")).status_code == 404


async def test_the_skill_and_interest_lists_are_sorted_by_name(ada: httpx.AsyncClient) -> None:
    skills = (await ada.get("/api/v1/skills")).json()
    interests = (await ada.get("/api/v1/interests")).json()

    assert {"slug": "python", "name": "Python", "category": "Programming languages"} in skills
    assert {"slug": "education", "name": "Education"} in interests
    for items in (skills, interests):
        names = [item["name"] for item in items]
        assert len(names) >= 20
        assert names == sorted(names, key=str.casefold)


# --- viewing other students -----------------------------------------------------------------


async def test_a_discoverable_profile_shows_only_public_fields(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    own = (await ada.put("/api/v1/me/profile", json=PROFILE)).json()

    response = await grace.get(f"/api/v1/profiles/{own['user_id']}")
    assert response.status_code == 200
    public = response.json()
    assert set(public) == PUBLIC_FIELDS
    assert {key: own[key] for key in PUBLIC_FIELDS} == public
    assert ADA not in response.text


async def test_students_can_preview_their_own_hidden_profile(ada: httpx.AsyncClient) -> None:
    own = (await ada.put("/api/v1/me/profile", json=profile(discoverable=False))).json()
    response = await ada.get(f"/api/v1/profiles/{own['user_id']}")
    assert response.status_code == 200
    assert set(response.json()) == PUBLIC_FIELDS


async def test_hidden_blocked_and_unavailable_profiles_all_look_like_missing_ones(
    app: object,
    mailer: MemoryMailer,
    ada: httpx.AsyncClient,
    grace: httpx.AsyncClient,
    clean_database: AsyncEngine,
) -> None:
    async def visible_profile(email: str, **changes: Any) -> tuple[httpx.AsyncClient, str]:
        client = await student(app, mailer, email)
        await client.put("/api/v1/me/profile", json=profile(**changes))
        return client, await user_id(client)

    hidden, hidden_id = await visible_profile("hidden@northeastern.edu", discoverable=False)
    blocked, blocked_id = await visible_profile("blocked@northeastern.edu")
    blocker, blocker_id = await visible_profile("blocker@northeastern.edu")
    suspended, suspended_id = await visible_profile("suspended@northeastern.edu")
    unverified, unverified_id = await visible_profile("unverified@northeastern.edu")
    no_profile = await student(app, mailer, "noprofile@northeastern.edu")
    no_profile_id = await user_id(no_profile)

    assert (await grace.post("/api/v1/blocks", json={"user_id": blocked_id})).status_code == 204
    assert (
        await blocker.post("/api/v1/blocks", json={"user_id": await user_id(grace)})
    ).status_code == 204
    await execute(
        clean_database, "UPDATE users SET suspended_at = now() WHERE id = :id", id=suspended_id
    )
    await execute(
        clean_database, "UPDATE users SET verified_at = NULL WHERE id = :id", id=unverified_id
    )

    missing = await grace.get(f"/api/v1/profiles/{uuid.uuid4()}")
    assert missing.status_code == 404
    for target in (hidden_id, blocked_id, blocker_id, suspended_id, unverified_id, no_profile_id):
        response = await grace.get(f"/api/v1/profiles/{target}")
        assert response.status_code == 404, target
        assert without_request_id(response) == without_request_id(missing)

    # A block hides both students from each other; a third student still sees both.
    assert (await blocked.get(f"/api/v1/profiles/{await user_id(grace)}")).status_code == 404
    for target in (blocked_id, blocker_id):
        assert (await ada.get(f"/api/v1/profiles/{target}")).status_code == 200

    for client in (hidden, blocked, blocker, suspended, unverified, no_profile):
        await client.aclose()


async def test_unverified_students_cannot_view_profiles(
    app: object, mailer: MemoryMailer, ada: httpx.AsyncClient
) -> None:
    own = (await ada.put("/api/v1/me/profile", json=PROFILE)).json()
    client = await student(app, mailer, "new@northeastern.edu", verified=False)
    try:
        response = await client.get(f"/api/v1/profiles/{own['user_id']}")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "verification_required"
    finally:
        await client.aclose()
