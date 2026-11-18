"""Suggested teammates for a project's owner: who may be suggested, into which role, that nothing
is ever enrolled, and that the answer never carries a score."""

import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.rate_limit import RateLimiter
from app.ml.team import OpenRole, TeamCandidate, TeamPick
from app.modules.teams import suggestions as suggestions_module
from app.modules.teams.schemas import TeamSuggestionsOut
from app.modules.teams.suggestions import TEAM_SUGGESTIONS_PER_USER, TeamSuggestionService
from tests.conftest import MemoryMailer, rolled_back_session
from tests.helpers import (
    block,
    make_admin,
    new_client,
    profile,
    scalar,
    student_with_profile,
    suspend,
    unverify,
    user_id,
)

# A cartesian product between the candidate query's tables would silently duplicate candidates;
# turn that warning into a failure so it can never slip in unnoticed.
pytestmark = [
    pytest.mark.usefixtures("clean_database"),
    pytest.mark.filterwarnings("error::sqlalchemy.exc.SAWarning"),
]

ADA = "ada@northeastern.edu"

# The owner's default week (PROFILE): Monday 18:00-21:00 and Wednesday 10:00-12:00, New York.
WEDNESDAY_HOUR = [{"weekday": 2, "start_min": 600, "end_min": 660}]
THURSDAY_ONLY = [{"weekday": 3, "start_min": 600, "end_min": 720}]

SUGGESTION_KEYS = {"user_id", "display_name", "headline", "role", "covers"}


def skills(*slugs: str) -> list[dict[str, str]]:
    return [{"slug": slug, "level": "intermediate"} for slug in slugs]


def suggestions_url(project_id: str) -> str:
    return f"/api/v1/projects/{project_id}/suggestions"


async def create_project(
    owner: httpx.AsyncClient, *, capacity: int, roles: list[dict[str, Any]]
) -> dict[str, Any]:
    created = await owner.post(
        "/api/v1/projects",
        json={"title": "Study planner", "summary": "", "capacity": capacity, "roles": roles},
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


def role_id(project: dict[str, Any], title: str) -> str:
    return str(next(role["id"] for role in project["roles"] if role["title"] == title))


async def join(
    owner: httpx.AsyncClient, applicant: httpx.AsyncClient, project_id: str, role: str | None
) -> None:
    body = {"role_id": role} if role is not None else {}
    applied = await applicant.post(f"/api/v1/projects/{project_id}/apply", json=body)
    assert applied.status_code == 201, applied.text
    accepted = await owner.post(
        f"/api/v1/projects/{project_id}/applications/{applied.json()['id']}/accept"
    )
    assert accepted.status_code == 200, accepted.text


async def suggest(owner: httpx.AsyncClient, project_id: str) -> dict[str, Any]:
    response = await owner.get(suggestions_url(project_id))
    assert response.status_code == 200, response.text
    return dict(response.json())


def suggested_ids(body: dict[str, Any]) -> list[str]:
    return [item["user_id"] for item in body["suggestions"]]


async def connect(a: httpx.AsyncClient, b: httpx.AsyncClient) -> None:
    sent = await a.post("/api/v1/connections", json={"user_id": await user_id(b)})
    assert sent.status_code == 201, sent.text
    accepted = await b.post(f"/api/v1/connections/{sent.json()['id']}/accept")
    assert accepted.status_code == 200, accepted.text


# --- who may ask --------------------------------------------------------------------------------


async def simple_project(owner: httpx.AsyncClient, *, capacity: int = 5) -> dict[str, Any]:
    return await create_project(
        owner,
        capacity=capacity,
        roles=[{"title": "Design", "open_slots": 3, "skills": ["figma", "ui-design"]}],
    )


async def test_anonymous_request_is_rejected(app: object, mailer: MemoryMailer) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    project = await simple_project(ada)
    async with new_client(app) as anonymous:
        response = await anonymous.get(suggestions_url(project["id"]))
    assert response.status_code == 401


async def test_only_the_owner_may_ask_everyone_else_gets_404(
    app: object, mailer: MemoryMailer
) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    member = await student_with_profile(app, mailer, "grace@northeastern.edu")
    stranger = await student_with_profile(app, mailer, "henry@northeastern.edu")
    project = await simple_project(ada)
    await join(ada, member, project["id"], role_id(project, "Design"))

    for caller in (member, stranger):
        response = await caller.get(suggestions_url(project["id"]))
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    unknown = await ada.get(suggestions_url(str(uuid.uuid4())))
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "not_found"

    assert (await ada.get(suggestions_url(project["id"]))).status_code == 200


async def test_a_full_project_gives_404(app: object, mailer: MemoryMailer) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    grace = await student_with_profile(app, mailer, "grace@northeastern.edu")
    project = await simple_project(ada, capacity=2)
    await join(ada, grace, project["id"], None)

    response = await ada.get(suggestions_url(project["id"]))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_a_closed_project_gives_404(app: object, mailer: MemoryMailer) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    project = await simple_project(ada)
    closed = await ada.patch(f"/api/v1/projects/{project['id']}", json={"status": "closed"})
    assert closed.status_code == 200, closed.text

    response = await ada.get(suggestions_url(project["id"]))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_an_owner_not_opted_in_gets_the_opt_in_error(
    app: object, mailer: MemoryMailer
) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    project = await simple_project(ada)
    hidden = await ada.put("/api/v1/me/profile", json=profile(discoverable=False))
    assert hidden.status_code == 200, hidden.text

    response = await ada.get(suggestions_url(project["id"]))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "discovery_opt_in_required"


async def test_an_owner_who_is_not_a_student_gets_the_students_only_error(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    project = await simple_project(ada)
    await make_admin(clean_database, await user_id(ada))

    response = await ada.get(suggestions_url(project["id"]))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "discovery_students_only"


async def test_an_owner_with_no_availability_is_asked_to_add_some(
    app: object, mailer: MemoryMailer
) -> None:
    # Nobody can share time with an owner who has none, so rather than "no one matches" forever,
    # the owner is told what to fix.
    ada = await student_with_profile(app, mailer, ADA, availability=[])
    project = await simple_project(ada)
    await student_with_profile(app, mailer, "henry@northeastern.edu", skills=skills("figma"))

    response = await ada.get(suggestions_url(project["id"]))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "availability_required"


async def test_suggestions_are_rate_limited(app: object, mailer: MemoryMailer) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    project = await simple_project(ada)
    for _ in range(TEAM_SUGGESTIONS_PER_USER.max_events):
        assert (await ada.get(suggestions_url(project["id"]))).status_code == 200

    limited = await ada.get(suggestions_url(project["id"]))
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1


# --- the end-to-end answer ----------------------------------------------------------------------


async def test_a_known_team_gets_the_exact_suggestions_roles_and_uncovered_skills(
    app: object, mailer: MemoryMailer
) -> None:
    # Ada has python, so Backend only still needs C++ and cloud computing.
    ada = await student_with_profile(app, mailer, ADA)
    project = await create_project(
        ada,
        capacity=5,
        roles=[
            {"title": "Backend", "open_slots": 1, "skills": ["python", "cpp", "cloud-computing"]},
            {"title": "Design", "open_slots": 2, "skills": ["figma", "ui-design"]},
            {"title": "Infra", "open_slots": 2, "skills": ["csharp", "computer-vision", "docker"]},
            # Created after Infra but sorts before it by title: a docker holder fits both
            # equally, and creation order decides.
            {"title": "Automation", "open_slots": 1, "skills": ["docker"]},
        ],
    )
    project_id = project["id"]
    # Grace takes one of Design's two places and brings figma: three places left on the team.
    grace = await student_with_profile(
        app, mailer, "grace@northeastern.edu", skills=skills("figma")
    )
    await join(ada, grace, project_id, role_id(project, "Design"))

    henry = await student_with_profile(
        app, mailer, "henry@northeastern.edu", skills=skills("cpp", "cloud-computing")
    )
    isabel = await student_with_profile(
        app, mailer, "isabel@northeastern.edu", skills=skills("ui-design")
    )
    lena = await student_with_profile(
        app,
        mailer,
        "lena@northeastern.edu",
        skills=skills("ui-design"),
        availability=WEDNESDAY_HOUR,
    )
    jamal = await student_with_profile(
        app, mailer, "jamal@northeastern.edu", skills=skills("docker"), availability=WEDNESDAY_HOUR
    )
    keith = await student_with_profile(app, mailer, "keith@northeastern.edu", skills=skills("cpp"))
    # Mona would still bring C#, but the team has no fourth place for her.
    mona = await student_with_profile(
        app,
        mailer,
        "mona@northeastern.edu",
        skills=skills("csharp"),
        availability=[{"weekday": 2, "start_min": 600, "end_min": 630}],
    )
    henry_id, isabel_id, jamal_id = (
        await user_id(henry),
        await user_id(isabel),
        await user_id(jamal),
    )
    lena_id, keith_id, mona_id = await user_id(lena), await user_id(keith), await user_id(mona)

    missing = (await ada.get(f"/api/v1/projects/{project_id}")).json()["missing_skills"]
    body = await suggest(ada, project_id)

    # Henry brings two needed skills; Isabel and Lena each bring UI design, and Isabel shares
    # five hours of Ada's week to Lena's one; then Jamal (an hour) beats Mona (half an hour) and
    # the team is full. Infra still has a place, so what it wants and nobody picked brings is
    # uncovered, C# included.
    assert body == {
        "suggestions": [
            {
                "user_id": henry_id,
                "display_name": "Henry",
                "headline": "Analytical engines and poetry",
                "role": {"id": role_id(project, "Backend"), "title": "Backend"},
                "covers": [
                    {"slug": "cpp", "name": "C++"},
                    {"slug": "cloud-computing", "name": "Cloud computing"},
                ],
            },
            {
                "user_id": isabel_id,
                "display_name": "Isabel",
                "headline": "Analytical engines and poetry",
                "role": {"id": role_id(project, "Design"), "title": "Design"},
                "covers": [{"slug": "ui-design", "name": "UI design"}],
            },
            {
                "user_id": jamal_id,
                "display_name": "Jamal",
                "headline": "Analytical engines and poetry",
                "role": {"id": role_id(project, "Infra"), "title": "Infra"},
                "covers": [{"slug": "docker", "name": "Docker"}],
            },
        ],
        "uncovered": [
            {"slug": "csharp", "name": "C#"},
            {"slug": "computer-vision", "name": "Computer vision"},
        ],
    }
    assert not {lena_id, keith_id, mona_id} & set(suggested_ids(body))

    # Never past the team's places or any role's room.
    detail = (await ada.get(f"/api/v1/projects/{project_id}")).json()
    assert len(body["suggestions"]) <= detail["capacity"] - detail["member_count"]
    per_role = Counter(item["role"]["id"] for item in body["suggestions"])
    for role in detail["roles"]:
        assert per_role[role["id"]] <= role["remaining_slots"]
    # What's suggested plus what's left is exactly what the project page says is missing.
    covered = [item for s in body["suggestions"] for item in s["covers"]]
    assert sorted(i["slug"] for i in covered + body["uncovered"]) == sorted(
        i["slug"] for i in missing
    )


async def test_nothing_is_ever_enrolled(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    project = await simple_project(ada)
    await student_with_profile(app, mailer, "henry@northeastern.edu", skills=skills("figma"))
    applicant = await student_with_profile(app, mailer, "jamal@northeastern.edu")
    await applicant.post(f"/api/v1/projects/{project['id']}/apply", json={})

    async def snapshot() -> tuple[object, object]:
        return (
            await scalar(
                clean_database,
                "SELECT string_agg(user_id::text || coalesce(role_id::text, ''), ',' "
                "ORDER BY user_id) FROM project_members",
            ),
            await scalar(
                clean_database,
                "SELECT string_agg(id::text || status, ',' ORDER BY id) FROM applications",
            ),
        )

    before = await snapshot()
    body = await suggest(ada, project["id"])
    assert body["suggestions"]  # something was suggested, and still nobody was enrolled
    assert await snapshot() == before


async def test_the_answer_has_exactly_the_public_keys_and_no_score(
    app: object, mailer: MemoryMailer
) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    project = await create_project(
        ada,
        capacity=5,
        roles=[{"title": "Design", "open_slots": 3, "skills": ["figma", "ui-design", "docker"]}],
    )
    await student_with_profile(app, mailer, "henry@northeastern.edu", skills=skills("figma"))

    response = await ada.get(suggestions_url(project["id"]))
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"suggestions", "uncovered"}
    assert len(body["suggestions"]) == 1
    for item in body["suggestions"]:
        assert set(item) == SUGGESTION_KEYS
        assert set(item["role"]) == {"id", "title"}
        assert all(set(cover) == {"slug", "name"} for cover in item["covers"])
    assert body["uncovered"] and all(set(u) == {"slug", "name"} for u in body["uncovered"])
    assert "score" not in response.text and "overlap" not in response.text


async def test_no_needed_skills_gives_an_empty_answer(app: object, mailer: MemoryMailer) -> None:
    # Ada already has python, the only skill the role asks for.
    ada = await student_with_profile(app, mailer, ADA)
    project = await create_project(
        ada, capacity=5, roles=[{"title": "Backend", "open_slots": 2, "skills": ["python"]}]
    )
    await student_with_profile(app, mailer, "henry@northeastern.edu", skills=skills("python"))

    assert await suggest(ada, project["id"]) == {"suggestions": [], "uncovered": []}


async def test_a_role_its_members_already_fill_is_not_suggested_for(
    app: object, mailer: MemoryMailer
) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    project = await create_project(
        ada,
        capacity=6,
        roles=[
            {"title": "Analyst", "open_slots": 1, "skills": ["data-analysis"]},
            {"title": "Design", "open_slots": 3, "skills": ["figma"]},
        ],
    )
    filler = await student_with_profile(app, mailer, "filler@northeastern.edu", skills=[])
    await join(ada, filler, project["id"], role_id(project, "Analyst"))
    analyst = await student_with_profile(
        app, mailer, "laura@northeastern.edu", skills=skills("data-analysis")
    )
    designer = await student_with_profile(
        app, mailer, "keith@northeastern.edu", skills=skills("figma")
    )

    body = await suggest(ada, project["id"])
    # Analyst's one place is taken, so data analysis isn't needed any more, even though the filler
    # doesn't have it.
    assert suggested_ids(body) == [await user_id(designer)]
    assert await user_id(analyst) not in suggested_ids(body)
    assert body["uncovered"] == []


async def test_at_most_five_students_are_suggested(app: object, mailer: MemoryMailer) -> None:
    slugs = ["docker", "figma", "go", "rust", "sql", "swift"]
    ada = await student_with_profile(app, mailer, ADA)
    project = await create_project(
        ada, capacity=10, roles=[{"title": "Anything", "open_slots": 9, "skills": slugs}]
    )
    for slug in slugs:
        await student_with_profile(app, mailer, f"{slug}@northeastern.edu", skills=skills(slug))

    body = await suggest(ada, project["id"])
    assert len(body["suggestions"]) == suggestions_module.MAX_TEAM_SUGGESTIONS == 5
    assert len(body["uncovered"]) == 1


async def test_the_pool_keeps_those_holding_the_most_needed_skills_and_some_time(
    app: object, mailer: MemoryMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With room to weigh only one candidate, the pool must spend it on whoever holds the most
    # needed skills among those with any availability at all: not on someone with more skills
    # overall, nor on someone who could never meet.
    monkeypatch.setattr(suggestions_module, "CANDIDATE_POOL", 1)
    ada = await student_with_profile(app, mailer, ADA)
    project = await create_project(
        ada,
        capacity=5,
        roles=[{"title": "Design", "open_slots": 3, "skills": ["figma", "ui-design", "docker"]}],
    )
    await student_with_profile(
        app,
        mailer,
        "busy@northeastern.edu",
        skills=skills("figma", "ui-design", "docker"),
        availability=[],
    )
    # Whichever of two students sorts first by id gets the broad profile, so id order alone would
    # always spend the one place on them.
    pair = [
        await student_with_profile(app, mailer, f"{name}@northeastern.edu", skills=[])
        for name in ("first", "second")
    ]
    ids = [await user_id(client) for client in pair]
    (broad, _), (focused, focused_id) = sorted(
        zip(pair, ids, strict=True), key=lambda p: uuid.UUID(p[1])
    )
    for client, name, held in (
        (broad, "Broad", skills("docker", "go", "rust", "swift", "kotlin")),
        (focused, "Focused", skills("figma", "ui-design")),
    ):
        saved = await client.put("/api/v1/me/profile", json=profile(display_name=name, skills=held))
        assert saved.status_code == 200, saved.text

    body = await suggest(ada, project["id"])
    assert suggested_ids(body) == [focused_id]


# --- each exclusion on its own ------------------------------------------------------------------
#
# Every case: the role wants figma and docker, and the team has exactly one place left. Henry
# (docker) is always eligible, so the answer is never empty for an unrelated reason; the student
# under test brings figma and shares more of the owner's week than Henry, so they'd take that one
# place unless excluded. That also proves the exclusion happens before the greedy pass: an
# excluded student dropped only afterwards would have taken Henry's place and left nobody.


async def exclusion_case(
    app: object, mailer: MemoryMailer, *, members: int = 1, places: int = 1, **changes: Any
) -> tuple[httpx.AsyncClient, dict[str, Any], httpx.AsyncClient, str]:
    """Returns the owner, the project (room for `members`, the owner included, plus `places`
    more), the student under test and Henry's id."""
    ada = await student_with_profile(app, mailer, ADA)
    project = await create_project(
        ada,
        capacity=members + places,
        roles=[{"title": "Design", "open_slots": 3, "skills": ["figma", "docker"]}],
    )
    henry = await student_with_profile(
        app, mailer, "henry@northeastern.edu", skills=skills("docker"), availability=WEDNESDAY_HOUR
    )
    subject = await student_with_profile(
        app, mailer, "subject@northeastern.edu", **{"skills": skills("figma"), **changes}
    )
    return ada, project, subject, await user_id(henry)


async def assert_excluded(
    owner: httpx.AsyncClient, project: dict[str, Any], subject_id: str, henry_id: str
) -> None:
    body = await suggest(owner, project["id"])
    assert suggested_ids(body) == [henry_id]
    assert subject_id not in suggested_ids(body)
    assert body["uncovered"] == [{"slug": "figma", "name": "Figma"}]


async def assert_included(
    owner: httpx.AsyncClient, project: dict[str, Any], subject: httpx.AsyncClient
) -> None:
    body = await suggest(owner, project["id"])
    assert suggested_ids(body) == [await user_id(subject)]
    assert body["uncovered"] == [{"slug": "docker", "name": "Docker"}]


async def test_the_student_under_test_is_suggested_when_nothing_excludes_them(
    app: object, mailer: MemoryMailer
) -> None:
    ada, project, subject, _ = await exclusion_case(app, mailer)
    await assert_included(ada, project, subject)


async def test_a_hidden_student_is_excluded(app: object, mailer: MemoryMailer) -> None:
    ada, project, subject, henry_id = await exclusion_case(app, mailer, discoverable=False)
    await assert_excluded(ada, project, await user_id(subject), henry_id)


async def test_a_hidden_student_is_excluded_even_when_connected_to_the_owner(
    app: object, mailer: MemoryMailer
) -> None:
    ada, project, subject, henry_id = await exclusion_case(app, mailer)
    await connect(ada, subject)
    hidden = await subject.put(
        "/api/v1/me/profile",
        json=profile(display_name="Subject", skills=skills("figma"), discoverable=False),
    )
    assert hidden.status_code == 200, hidden.text
    await assert_excluded(ada, project, await user_id(subject), henry_id)


async def test_a_suspended_student_is_excluded(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada, project, subject, henry_id = await exclusion_case(app, mailer)
    subject_id = await user_id(subject)
    await suspend(clean_database, subject_id)
    await assert_excluded(ada, project, subject_id, henry_id)


async def test_an_unverified_student_is_excluded(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada, project, subject, henry_id = await exclusion_case(app, mailer)
    subject_id = await user_id(subject)
    await unverify(clean_database, subject_id)
    await assert_excluded(ada, project, subject_id, henry_id)


async def test_an_admin_is_excluded(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada, project, subject, henry_id = await exclusion_case(app, mailer)
    subject_id = await user_id(subject)
    await make_admin(clean_database, subject_id)
    await assert_excluded(ada, project, subject_id, henry_id)


async def test_a_student_the_owner_blocked_is_excluded(app: object, mailer: MemoryMailer) -> None:
    ada, project, subject, henry_id = await exclusion_case(app, mailer)
    subject_id = await user_id(subject)
    await block(ada, subject_id)
    await assert_excluded(ada, project, subject_id, henry_id)


async def test_a_student_who_blocked_the_owner_is_excluded(
    app: object, mailer: MemoryMailer
) -> None:
    ada, project, subject, henry_id = await exclusion_case(app, mailer)
    await block(subject, await user_id(ada))
    await assert_excluded(ada, project, await user_id(subject), henry_id)


async def member_and_subject(
    app: object, mailer: MemoryMailer
) -> tuple[httpx.AsyncClient, dict[str, Any], httpx.AsyncClient, httpx.AsyncClient, str]:
    ada, project, subject, henry_id = await exclusion_case(app, mailer, members=2)
    grace = await student_with_profile(app, mailer, "grace@northeastern.edu", skills=[])
    await join(ada, grace, project["id"], None)
    return ada, project, subject, grace, henry_id


async def test_a_student_a_team_member_blocked_is_excluded(
    app: object, mailer: MemoryMailer
) -> None:
    ada, project, subject, grace, henry_id = await member_and_subject(app, mailer)
    subject_id = await user_id(subject)
    await block(grace, subject_id)
    await assert_excluded(ada, project, subject_id, henry_id)


async def test_a_student_who_blocked_a_team_member_is_excluded(
    app: object, mailer: MemoryMailer
) -> None:
    ada, project, subject, grace, henry_id = await member_and_subject(app, mailer)
    await block(subject, await user_id(grace))
    await assert_excluded(ada, project, await user_id(subject), henry_id)


async def test_a_block_with_someone_off_the_team_excludes_nobody(
    app: object, mailer: MemoryMailer
) -> None:
    ada, project, subject, _ = await exclusion_case(app, mailer)
    outsider = await student_with_profile(app, mailer, "outsider@northeastern.edu", skills=[])
    await block(outsider, await user_id(subject))
    await assert_included(ada, project, subject)


async def test_a_student_unwilling_to_join_projects_is_excluded(
    app: object, mailer: MemoryMailer
) -> None:
    ada, project, subject, henry_id = await exclusion_case(app, mailer, goals=["research"])
    await assert_excluded(ada, project, await user_id(subject), henry_id)


async def test_a_current_member_is_never_suggested(app: object, mailer: MemoryMailer) -> None:
    ada, project, subject, henry_id = await exclusion_case(app, mailer, members=2)
    await join(ada, subject, project["id"], role_id(project, "Design"))

    body = await suggest(ada, project["id"])
    # Their figma now counts as the team's, so it isn't missing any more either.
    assert suggested_ids(body) == [henry_id]
    assert body["uncovered"] == []


async def test_a_pending_applicant_is_excluded(app: object, mailer: MemoryMailer) -> None:
    ada, project, subject, henry_id = await exclusion_case(app, mailer)
    applied = await subject.post(f"/api/v1/projects/{project['id']}/apply", json={})
    assert applied.status_code == 201, applied.text
    await assert_excluded(ada, project, await user_id(subject), henry_id)


async def test_a_rejected_applicant_may_be_suggested_again(
    app: object, mailer: MemoryMailer
) -> None:
    ada, project, subject, _ = await exclusion_case(app, mailer)
    applied = (await subject.post(f"/api/v1/projects/{project['id']}/apply", json={})).json()
    rejected = await ada.post(
        f"/api/v1/projects/{project['id']}/applications/{applied['id']}/reject"
    )
    assert rejected.status_code == 200, rejected.text
    await assert_included(ada, project, subject)


async def test_a_student_with_no_time_in_common_with_the_owner_is_excluded(
    app: object, mailer: MemoryMailer
) -> None:
    # Two places: with no shared time the student would lose the first to Henry anyway, so only
    # the second shows whether they were let through at all.
    ada, project, subject, henry_id = await exclusion_case(
        app, mailer, places=2, availability=THURSDAY_ONLY
    )
    await assert_excluded(ada, project, await user_id(subject), henry_id)


async def test_a_student_with_no_availability_is_excluded(
    app: object, mailer: MemoryMailer
) -> None:
    ada, project, subject, henry_id = await exclusion_case(app, mailer, availability=[])
    await assert_excluded(ada, project, await user_id(subject), henry_id)


async def test_a_connected_student_is_still_suggested(app: object, mailer: MemoryMailer) -> None:
    ada, project, subject, _ = await exclusion_case(app, mailer)
    await connect(subject, ada)
    await assert_included(ada, project, subject)


# --- changes between the candidate query and the card query -----------------------------------


def after_the_candidate_query(
    monkeypatch: pytest.MonkeyPatch, change: Callable[[], Awaitable[object]]
) -> None:
    """Runs `change` once, after the candidate query and before the card query: loading the
    candidates' availability sits between the two."""
    real_load_slots = suggestions_module.load_slots
    pending = [change]

    async def load_slots_after_a_change(session: Any, user_ids: Any) -> Any:
        while pending:
            await pending.pop()()
        return await real_load_slots(session, user_ids)

    monkeypatch.setattr(suggestions_module, "load_slots", load_slots_after_a_change)


async def hide(client: httpx.AsyncClient, name: str, *slugs: str) -> None:
    hidden = await client.put(
        "/api/v1/me/profile",
        json=profile(display_name=name, skills=skills(*slugs), discoverable=False),
    )
    assert hidden.status_code == 200, hidden.text


async def test_a_pick_who_vanishes_before_the_cards_are_read_gives_their_place_to_the_next(
    app: object, mailer: MemoryMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    ada = await student_with_profile(app, mailer, ADA)
    project = await create_project(
        ada,
        capacity=5,
        roles=[
            {"title": "Backend", "open_slots": 1, "skills": ["cpp", "sql"]},
            {"title": "Design", "open_slots": 2, "skills": ["figma"]},
        ],
    )
    # Vera shares the most time and alone would take Backend, which would leave SQL needed by
    # nobody with room. She hides right after the candidate query.
    vera = await student_with_profile(app, mailer, "vera@northeastern.edu", skills=skills("cpp"))
    will = await student_with_profile(
        app,
        mailer,
        "will@northeastern.edu",
        skills=skills("figma"),
        availability=[{"weekday": 2, "start_min": 600, "end_min": 720}],
    )
    xena = await student_with_profile(
        app, mailer, "xena@northeastern.edu", skills=skills("sql"), availability=WEDNESDAY_HOUR
    )
    after_the_candidate_query(monkeypatch, lambda: hide(vera, "Vera", "cpp"))

    body = await suggest(ada, project["id"])

    # Without Vera, Xena takes the Backend place she'd have had, and C++ is the one thing left.
    assert [(s["user_id"], s["role"]["title"], s["covers"]) for s in body["suggestions"]] == [
        (await user_id(will), "Design", [{"slug": "figma", "name": "Figma"}]),
        (await user_id(xena), "Backend", [{"slug": "sql", "name": "SQL"}]),
    ]
    assert body["uncovered"] == [{"slug": "cpp", "name": "C++"}]
    missing = (await ada.get(f"/api/v1/projects/{project['id']}")).json()["missing_skills"]
    covered = [item for s in body["suggestions"] for item in s["covers"]]
    assert sorted(i["slug"] for i in covered + body["uncovered"]) == sorted(
        i["slug"] for i in missing
    )


async def test_a_connected_student_who_hides_before_the_cards_are_read_is_dropped(
    app: object, mailer: MemoryMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Still visible to the owner through the connection, but no longer opted in: the card query
    # applies the whole eligibility check again, not just visibility.
    ada, project, subject, henry_id = await exclusion_case(app, mailer)
    await connect(ada, subject)
    after_the_candidate_query(monkeypatch, lambda: hide(subject, "Subject", "figma"))

    await assert_excluded(ada, project, await user_id(subject), henry_id)


async def test_a_student_a_member_blocks_before_the_cards_are_read_is_dropped(
    app: object, mailer: MemoryMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The owner can still see them; only the team-wide block check catches it.
    ada, project, subject, grace, henry_id = await member_and_subject(app, mailer)
    subject_id = await user_id(subject)
    after_the_candidate_query(monkeypatch, lambda: block(grace, subject_id))

    await assert_excluded(ada, project, subject_id, henry_id)


async def test_the_greedy_pass_is_run_at_most_three_times(
    app: object, mailer: MemoryMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A pick who vanishes on every pass (a hidden student slipped in each time): after the third
    # pass the answer stands without them, and what they'd have covered is uncovered.
    ada, project, subject, henry_id = await exclusion_case(
        app, mailer, places=2, discoverable=False
    )
    ghost_id = uuid.UUID(await user_id(subject))
    real_greedy = suggestions_module.greedy_team
    passes = 0

    def greedy_with_a_ghost(
        candidates: Sequence[TeamCandidate],
        roles: Sequence[OpenRole],
        needed: frozenset[str],
        capacity_left: int,
        limit: int,
    ) -> tuple[list[TeamPick], frozenset[str]]:
        nonlocal passes
        passes += 1
        if passes > 10:
            raise RuntimeError("the greedy pass keeps being re-run")
        picks, uncovered = real_greedy(candidates, roles, needed, capacity_left, limit)
        ghost = TeamPick(user_id=ghost_id, role_id=roles[0].role_id, covers=frozenset({"figma"}))
        return [ghost, *picks], uncovered - ghost.covers

    monkeypatch.setattr(suggestions_module, "greedy_team", greedy_with_a_ghost)

    await assert_excluded(ada, project, str(ghost_id), henry_id)
    assert passes == suggestions_module.MAX_PASSES == 3


# --- query count: constant regardless of how many candidates there are ------------------------


async def _count_statements(engine: AsyncEngine, run: Callable[[], Awaitable[object]]) -> int:
    statements: list[str] = []

    def listener(conn: object, cursor: object, stmt: str, *args: object) -> None:
        statements.append(stmt)

    event.listen(engine.sync_engine, "before_cursor_execute", listener)
    try:
        await run()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", listener)
    return len([s for s in statements if not s.startswith("SAVEPOINT")])


async def test_the_statement_count_does_not_grow_with_the_candidates(
    app: object, mailer: MemoryMailer, engine: AsyncEngine
) -> None:
    async def scenario(n: int) -> tuple[uuid.UUID, uuid.UUID]:
        owner = await student_with_profile(app, mailer, f"owner{n}@northeastern.edu")
        project = await create_project(
            owner,
            capacity=10,
            roles=[{"title": "Design", "open_slots": 9, "skills": ["figma", "docker", "go"]}],
        )
        slugs = ["figma", "docker", "go"]
        for i in range(n):
            await student_with_profile(
                app,
                mailer,
                f"owner{n}-candidate{i}@northeastern.edu",
                skills=skills(slugs[i % 3]),
            )
        return uuid.UUID(await user_id(owner)), uuid.UUID(project["id"])

    async def count_for(n: int) -> int:
        owner_id, project_id = await scenario(n)
        async with rolled_back_session(engine) as session:
            service = TeamSuggestionService(session, RateLimiter())
            answers: list[TeamSuggestionsOut] = []

            async def run() -> None:
                answers.append(await service.suggestions(owner_id, project_id))

            count = await _count_statements(engine, run)
        assert answers[0].suggestions  # every scenario reaches the card query
        return count

    # The owner and project, three for what the team still needs, the candidates, everyone's
    # availability, and the cards.
    assert await count_for(2) == await count_for(6) == 7
