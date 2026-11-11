import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from app.core.rate_limit import Limit, RateLimiter
from app.modules.teams import service as team_service
from app.modules.teams.service import TeamService
from tests.conftest import MemoryMailer
from tests.helpers import PROFILE, execute, new_client, scalar, student, user_id

pytestmark = pytest.mark.usefixtures("clean_database")

# Eleven distinct, real slugs (migration 0003), for the "too many skills" case.
ELEVEN_SKILLS = [
    "python",
    "javascript",
    "typescript",
    "java",
    "c",
    "cpp",
    "csharp",
    "go",
    "rust",
    "kotlin",
    "swift",
]


async def with_profile(
    app: object, mailer: MemoryMailer, email: str, **changes: Any
) -> httpx.AsyncClient:
    client = await student(app, mailer, email)
    name = email.split("@")[0].title()
    response = await client.put(
        "/api/v1/me/profile", json={**PROFILE, "display_name": name, **changes}
    )
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
async def ada(app: object, mailer: MemoryMailer) -> AsyncIterator[httpx.AsyncClient]:
    client = await with_profile(app, mailer, "ada@northeastern.edu")
    yield client
    await client.aclose()


@pytest.fixture
async def grace(app: object, mailer: MemoryMailer) -> AsyncIterator[httpx.AsyncClient]:
    client = await with_profile(app, mailer, "grace@northeastern.edu")
    yield client
    await client.aclose()


@pytest.fixture
async def eve(app: object, mailer: MemoryMailer) -> AsyncIterator[httpx.AsyncClient]:
    client = await with_profile(app, mailer, "eve@northeastern.edu")
    yield client
    await client.aclose()


@pytest.fixture
async def frank(app: object, mailer: MemoryMailer) -> AsyncIterator[httpx.AsyncClient]:
    client = await with_profile(app, mailer, "frank@northeastern.edu")
    yield client
    await client.aclose()


def project_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": "Music Recommendation Engine",
        "summary": "Building a music recommender for study playlists.",
        "capacity": 5,
        "roles": [{"title": "ML Engineer", "open_slots": 1, "skills": ["machine-learning"]}],
    }
    body.update(overrides)
    return body


async def create_project(client: httpx.AsyncClient, **overrides: Any) -> httpx.Response:
    return await client.post("/api/v1/projects", json=project_body(**overrides))


async def created_project(client: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    response = await create_project(client, **overrides)
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


async def apply(
    client: httpx.AsyncClient, project_id: str, role_id: str | None = None
) -> dict[str, Any]:
    response = await client.post(f"/api/v1/projects/{project_id}/apply", json={"role_id": role_id})
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


async def act(
    client: httpx.AsyncClient, project_id: str, application_id: str, action: str
) -> httpx.Response:
    return await client.post(
        f"/api/v1/projects/{project_id}/applications/{application_id}/{action}"
    )


@asynccontextmanager
async def locked_row(
    engine: AsyncEngine, table: str, row_id: str, mode: str
) -> AsyncIterator[AsyncConnection]:
    """Holds a FOR UPDATE or FOR SHARE lock on one row, on its own connection, so a test can
    prove a request waits for it instead of relying on scheduler luck."""
    async with engine.connect() as holder:
        await holder.execute(
            text(f"SELECT id FROM {table} WHERE id = :id FOR {mode}"),  # noqa: S608 - fixed, test-only
            {"id": row_id},
        )
        try:
            yield holder
        finally:
            await holder.rollback()


def locked_project(engine: AsyncEngine, project_id: str, mode: str) -> Any:
    return locked_row(engine, "projects", project_id, mode)


# --- create -----------------------------------------------------------------------------------


async def test_creating_a_project_makes_its_owner_the_only_member(
    ada: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    ada_id = await user_id(ada)
    response = await create_project(
        ada,
        capacity=3,
        roles=[{"title": "ML Engineer", "open_slots": 1, "skills": ["machine-learning", "react"]}],
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "open"
    assert body["member_count"] == 1
    assert [m["user_id"] for m in body["members"]] == [ada_id]
    assert body["members"][0]["member_role"] == "owner"
    [role] = body["roles"]
    assert {s["slug"] for s in role["skills"]} == {"machine-learning", "react"}
    assert role["remaining_slots"] == 1
    assert body["conversation_id"] is not None
    # Ada's profile "has" machine-learning (the fixture's default skills) but not react.
    assert [s["slug"] for s in body["missing_skills"]] == ["react"]

    conv_row = await scalar(
        clean_database,
        "SELECT kind || '|' || project_id::text FROM conversations WHERE project_id = :pid",
        pid=body["id"],
    )
    assert conv_row == f"project|{body['id']}"
    active_members = await scalar(
        clean_database,
        "SELECT count(*) FROM conversation_members cm JOIN conversations c "
        "ON c.id = cm.conversation_id WHERE c.project_id = :pid AND cm.active",
        pid=body["id"],
    )
    assert active_members == 1


async def test_capacity_one_is_full_at_once(ada: httpx.AsyncClient) -> None:
    body = await created_project(ada, capacity=1, roles=[])
    assert body["status"] == "full"


async def test_creating_needs_a_verified_student(app: object, mailer: MemoryMailer) -> None:
    unverified = await student(app, mailer, "new@northeastern.edu", verified=False)
    async with new_client(app) as anonymous:
        assert (await create_project(anonymous)).status_code == 401
    response = await create_project(unverified)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "verification_required"
    await unverified.aclose()


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": ""},
        {"title": "x" * 141},
        {"title": "line one\nline two"},
        {"summary": "x" * 2001},
        {"capacity": 0},
        {"capacity": 51},
        {"roles": [{"title": f"Role {i}", "open_slots": 1, "skills": []} for i in range(11)]},
        {"roles": [{"title": "", "open_slots": 1, "skills": []}]},
        {"roles": [{"title": "x" * 81, "open_slots": 1, "skills": []}]},
        {"roles": [{"title": "Role", "open_slots": 0, "skills": []}]},
        {"roles": [{"title": "Role", "open_slots": 51, "skills": []}]},
        {"roles": [{"title": "Role", "open_slots": 1, "skills": ELEVEN_SKILLS}]},
        {"roles": [{"title": "Role", "open_slots": 1, "skills": ["python", "python"]}]},
        {"unexpected_field": True},
    ],
    ids=[
        "empty title",
        "141-char title",
        "newline in title",
        "2001-char summary",
        "capacity 0",
        "capacity 51",
        "11 roles",
        "empty role title",
        "81-char role title",
        "open_slots 0",
        "open_slots 51",
        "11 skills",
        "duplicate skills",
        "extra field",
    ],
)
async def test_invalid_projects_are_refused(
    ada: httpx.AsyncClient, overrides: dict[str, Any]
) -> None:
    response = await create_project(ada, **overrides)
    assert response.status_code == 422, response.text


async def test_unknown_skills_are_refused(ada: httpx.AsyncClient) -> None:
    response = await create_project(
        ada, roles=[{"title": "Role", "open_slots": 1, "skills": ["not-a-real-skill"]}]
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_skill"


async def test_creating_is_rate_limited(
    ada: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(team_service, "CREATE_LIMIT", Limit(max_events=1, window_seconds=3600))
    assert (await create_project(ada)).status_code == 201
    limited = await create_project(ada)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1


# --- list and detail ----------------------------------------------------------------------------


async def test_list_shows_open_projects_newest_first_with_paging(
    app: object, mailer: MemoryMailer, ada: httpx.AsyncClient
) -> None:
    owner = await with_profile(app, mailer, "owner@northeastern.edu")
    opens = [
        await created_project(
            owner,
            title=f"Open {i}",
            capacity=5,
            roles=[{"title": "Dev", "open_slots": 2, "skills": []}],
        )
        for i in range(3)
    ]
    closed = await created_project(owner, title="Closed", capacity=5, roles=[])
    await owner.patch(f"/api/v1/projects/{closed['id']}", json={"status": "closed"})
    await created_project(owner, title="Full", capacity=1, roles=[])

    page1 = await ada.get("/api/v1/projects", params={"limit": 2, "offset": 0})
    assert page1.status_code == 200, page1.text
    body1 = page1.json()
    assert [item["id"] for item in body1["items"]] == [opens[2]["id"], opens[1]["id"]]
    assert body1["next_offset"] == 2
    assert body1["items"][0]["member_count"] == 1
    assert body1["items"][0]["owner"] == {"user_id": await user_id(owner), "display_name": "Owner"}
    assert body1["items"][0]["roles"][0]["remaining_slots"] == 2

    page2 = await ada.get("/api/v1/projects", params={"limit": 2, "offset": 2})
    body2 = page2.json()
    assert [item["id"] for item in body2["items"]] == [opens[0]["id"]]
    assert body2["next_offset"] is None
    await owner.aclose()


async def test_list_hides_projects_blocked_either_direction(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    by_grace = await created_project(grace, title="By Grace", capacity=5, roles=[])

    await grace.post("/api/v1/blocks", json={"user_id": ada_id})
    assert by_grace["id"] not in [
        p["id"] for p in (await ada.get("/api/v1/projects")).json()["items"]
    ]
    await grace.delete(f"/api/v1/blocks/{ada_id}")
    assert by_grace["id"] in [p["id"] for p in (await ada.get("/api/v1/projects")).json()["items"]]

    await ada.post("/api/v1/blocks", json={"user_id": grace_id})
    assert by_grace["id"] not in [
        p["id"] for p in (await ada.get("/api/v1/projects")).json()["items"]
    ]


@pytest.mark.parametrize("limit", [0, 51])
async def test_list_limit_out_of_range_is_refused(ada: httpx.AsyncClient, limit: int) -> None:
    response = await ada.get("/api/v1/projects", params={"limit": limit})
    assert response.status_code == 422


async def test_detail_404_for_missing_or_blocked(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    missing = await ada.get(f"/api/v1/projects/{uuid.uuid4()}")
    assert missing.status_code == 404
    project = await created_project(grace, capacity=5, roles=[])
    await grace.post("/api/v1/blocks", json={"user_id": await user_id(ada)})
    blocked = await ada.get(f"/api/v1/projects/{project['id']}")
    assert blocked.status_code == 404
    assert blocked.json()["error"]["code"] == missing.json()["error"]["code"]


async def test_full_and_closed_projects_stay_visible_by_id(ada: httpx.AsyncClient) -> None:
    full = await created_project(ada, capacity=1, roles=[])
    closed = await created_project(ada, capacity=5, roles=[])
    await ada.patch(f"/api/v1/projects/{closed['id']}", json={"status": "closed"})
    assert (await ada.get(f"/api/v1/projects/{full['id']}")).status_code == 200
    assert (await ada.get(f"/api/v1/projects/{closed['id']}")).status_code == 200


async def test_conversation_id_shown_to_members_only(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    owner_view = (await ada.get(f"/api/v1/projects/{project['id']}")).json()
    assert owner_view["conversation_id"] is not None
    outsider_view = (await grace.get(f"/api/v1/projects/{project['id']}")).json()
    assert outsider_view["conversation_id"] is None


async def test_viewer_application_is_the_newest(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    first = await apply(grace, pid)
    await act(grace, pid, first["id"], "withdraw")
    second = await apply(grace, pid)

    detail = (await grace.get(f"/api/v1/projects/{pid}")).json()
    assert detail["viewer_application"] == {
        "id": second["id"],
        "status": "pending",
        "role_id": None,
    }


async def test_blocked_members_excluded_from_list_but_counted(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, eve: httpx.AsyncClient
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    applied = await apply(grace, pid)
    await act(ada, pid, applied["id"], "accept")

    await grace.post("/api/v1/blocks", json={"user_id": await user_id(eve)})
    detail = (await eve.get(f"/api/v1/projects/{pid}")).json()
    assert await user_id(grace) not in [m["user_id"] for m in detail["members"]]
    assert detail["member_count"] == 2


# --- my projects --------------------------------------------------------------------------------


async def test_my_projects_lists_owned_and_member_projects_of_any_status(
    ada: httpx.AsyncClient,
    grace: httpx.AsyncClient,
    eve: httpx.AsyncClient,
    frank: httpx.AsyncClient,
) -> None:
    owned_open = await created_project(ada, title="Owned Open", capacity=5, roles=[])
    owned_full = await created_project(ada, title="Owned Full", capacity=1, roles=[])
    owned_closed = await created_project(ada, title="Owned Closed", capacity=5, roles=[])
    await ada.patch(f"/api/v1/projects/{owned_closed['id']}", json={"status": "closed"})

    # A different owner from the one who blocks ada below, so that block only affects the one
    # project it names, not every project ada happens to be a member of.
    member_of = await created_project(frank, title="Frank's team", capacity=5, roles=[])
    joined = await apply(ada, member_of["id"])
    await act(frank, member_of["id"], joined["id"], "accept")

    applied_only = await created_project(eve, title="Eve's team", capacity=5, roles=[])
    await apply(ada, applied_only["id"])  # applied, but never accepted: not "my project"

    removed_from = await created_project(grace, title="Removed from", capacity=5, roles=[])
    removed_app = await apply(ada, removed_from["id"])
    await act(grace, removed_from["id"], removed_app["id"], "accept")
    await grace.post("/api/v1/blocks", json={"user_id": await user_id(ada)})  # removes ada

    response = await ada.get("/api/v1/me/projects")
    assert response.status_code == 200, response.text
    ids = {p["id"] for p in response.json()["items"]}
    assert ids == {owned_open["id"], owned_full["id"], owned_closed["id"], member_of["id"]}
    assert applied_only["id"] not in ids
    assert removed_from["id"] not in ids


async def test_my_projects_pages_newest_first(ada: httpx.AsyncClient) -> None:
    created = [
        await created_project(ada, title=f"Project {i}", capacity=5, roles=[]) for i in range(3)
    ]
    page1 = await ada.get("/api/v1/me/projects", params={"limit": 2, "offset": 0})
    assert page1.status_code == 200, page1.text
    body1 = page1.json()
    assert [p["id"] for p in body1["items"]] == [created[2]["id"], created[1]["id"]]
    assert body1["next_offset"] == 2

    page2 = await ada.get("/api/v1/me/projects", params={"limit": 2, "offset": 2})
    body2 = page2.json()
    assert [p["id"] for p in body2["items"]] == [created[0]["id"]]
    assert body2["next_offset"] is None


@pytest.mark.parametrize("limit", [0, 51])
async def test_my_projects_limit_out_of_range_is_refused(
    ada: httpx.AsyncClient, limit: int
) -> None:
    response = await ada.get("/api/v1/me/projects", params={"limit": limit})
    assert response.status_code == 422


# --- missing skills -------------------------------------------------------------------------


async def test_missing_skills_shrinks_when_a_member_with_it_joins(
    app: object, mailer: MemoryMailer, ada: httpx.AsyncClient
) -> None:
    designer = await with_profile(
        app, mailer, "designer@northeastern.edu", skills=[{"slug": "figma", "level": "advanced"}]
    )
    project = await created_project(
        ada, capacity=5, roles=[{"title": "Designer", "open_slots": 2, "skills": ["figma"]}]
    )
    pid, role_id = project["id"], project["roles"][0]["id"]
    before = (await ada.get(f"/api/v1/projects/{pid}")).json()
    assert [s["slug"] for s in before["missing_skills"]] == ["figma"]

    applied = await apply(designer, pid, role_id)
    await act(ada, pid, applied["id"], "accept")

    after = (await ada.get(f"/api/v1/projects/{pid}")).json()
    assert after["missing_skills"] == []
    await designer.aclose()


async def test_full_roles_skills_are_not_missing(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    project = await created_project(
        ada, capacity=5, roles=[{"title": "Designer", "open_slots": 1, "skills": ["figma"]}]
    )
    pid, role_id = project["id"], project["roles"][0]["id"]
    applied = await apply(grace, pid, role_id)  # grace doesn't have figma, but the role fills up
    await act(ada, pid, applied["id"], "accept")

    detail = (await ada.get(f"/api/v1/projects/{pid}")).json()
    assert detail["roles"][0]["remaining_slots"] == 0
    assert detail["missing_skills"] == []


# --- apply ------------------------------------------------------------------------------------


async def test_applying_twice_leaves_one_row(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    first = await apply(grace, pid)
    second = await apply(grace, pid)
    assert first["id"] == second["id"]
    assert (
        await scalar(
            clean_database, "SELECT count(*) FROM applications WHERE project_id = :pid", pid=pid
        )
    ) == 1


async def test_concurrent_applies_leave_one_row(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    responses = await asyncio.gather(
        *[grace.post(f"/api/v1/projects/{pid}/apply", json={}) for _ in range(5)]
    )
    assert [r.status_code for r in responses] == [201] * 5
    assert len({r.json()["id"] for r in responses}) == 1
    assert (
        await scalar(
            clean_database, "SELECT count(*) FROM applications WHERE project_id = :pid", pid=pid
        )
    ) == 1


async def test_owner_cannot_apply(ada: httpx.AsyncClient) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    response = await ada.post(f"/api/v1/projects/{project['id']}/apply", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "cannot_apply_to_own_project"


async def test_role_from_another_project_is_refused(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, eve: httpx.AsyncClient
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    other = await created_project(
        grace, capacity=5, roles=[{"title": "Dev", "open_slots": 1, "skills": []}]
    )
    response = await eve.post(
        f"/api/v1/projects/{project['id']}/apply", json={"role_id": other["roles"][0]["id"]}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_role"


async def test_members_cannot_apply_again(ada: httpx.AsyncClient, grace: httpx.AsyncClient) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    applied = await apply(grace, pid)
    await act(ada, pid, applied["id"], "accept")
    response = await grace.post(f"/api/v1/projects/{pid}/apply", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_a_member"


async def test_closed_project_refuses_applications(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    await ada.patch(f"/api/v1/projects/{project['id']}", json={"status": "closed"})
    response = await grace.post(f"/api/v1/projects/{project['id']}/apply", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "project_closed"


async def test_full_project_refuses_applications(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    project = await created_project(ada, capacity=1, roles=[])
    response = await grace.post(f"/api/v1/projects/{project['id']}/apply", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "project_full"


async def test_full_role_refuses_applications(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, eve: httpx.AsyncClient
) -> None:
    project = await created_project(
        ada, capacity=5, roles=[{"title": "Dev", "open_slots": 1, "skills": []}]
    )
    pid, role_id = project["id"], project["roles"][0]["id"]
    applied = await apply(grace, pid, role_id)
    await act(ada, pid, applied["id"], "accept")
    response = await eve.post(f"/api/v1/projects/{pid}/apply", json={"role_id": role_id})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "role_full"


async def test_blocked_students_cannot_apply_either_way(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    ada_id = await user_id(ada)
    project1 = await created_project(ada, capacity=5, roles=[])
    await grace.post("/api/v1/blocks", json={"user_id": ada_id})
    assert (
        await grace.post(f"/api/v1/projects/{project1['id']}/apply", json={})
    ).status_code == 404
    await grace.delete(f"/api/v1/blocks/{ada_id}")

    project2 = await created_project(ada, capacity=5, roles=[])
    await ada.post("/api/v1/blocks", json={"user_id": await user_id(grace)})
    assert (
        await grace.post(f"/api/v1/projects/{project2['id']}/apply", json={})
    ).status_code == 404


async def test_applying_is_rate_limited(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(team_service, "APPLY_LIMIT", Limit(max_events=1, window_seconds=3600))
    project1 = await created_project(ada, capacity=5, roles=[])
    project2 = await created_project(ada, capacity=5, roles=[])
    assert (
        await grace.post(f"/api/v1/projects/{project1['id']}/apply", json={})
    ).status_code == 201
    limited = await grace.post(f"/api/v1/projects/{project2['id']}/apply", json={})
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1


async def test_apply_again_after_rejection_or_withdrawal(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    first = await apply(grace, pid)
    await act(ada, pid, first["id"], "reject")
    second = await apply(grace, pid)
    assert second["id"] != first["id"]

    await act(grace, pid, second["id"], "withdraw")
    third = await apply(grace, pid)
    assert third["id"] != second["id"]
    assert (
        await scalar(
            clean_database, "SELECT count(*) FROM applications WHERE project_id = :pid", pid=pid
        )
    ) == 3


async def test_apply_does_not_500_when_the_application_is_decided_right_after_commit(
    ada: httpx.AsyncClient,
    grace: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    clean_database: AsyncEngine,
) -> None:
    """Reproduces a decision landing in the gap between apply()'s own commit and its read-back:
    the old code re-read the row in a brand new transaction and 500'd (NoResultFound) when it
    was no longer pending. Deterministic: the withdraw is injected right after the real commit
    runs, not timed by the scheduler."""
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    grace_id = await user_id(grace)
    original_commit = AsyncSession.commit
    fired = False

    async def commit_then_decide(self: AsyncSession) -> None:
        nonlocal fired
        await original_commit(self)
        if not fired:
            fired = True
            await execute(
                clean_database,
                "UPDATE applications SET status = 'withdrawn', decided_at = now() "
                "WHERE project_id = :pid AND applicant_id = :uid AND status = 'pending'",
                pid=pid,
                uid=grace_id,
            )

    monkeypatch.setattr(AsyncSession, "commit", commit_then_decide)
    response = await grace.post(f"/api/v1/projects/{pid}/apply", json={"role_id": None})
    assert response.status_code == 201, response.text


async def test_apply_read_back_falls_back_to_the_latest_application_if_pending_is_gone(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    """Calls the read-back helper directly against a state where the pending row has already
    been decided, so the fallback is proven without relying on scheduler timing."""
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    applied = await apply(grace, pid)
    await act(grace, pid, applied["id"], "withdraw")

    async with async_sessionmaker(engine)() as session:
        service = TeamService(session, RateLimiter())
        application, _, _ = await service._pending_or_latest(
            uuid.UUID(pid), uuid.UUID(await user_id(grace))
        )
        assert application.id == uuid.UUID(applied["id"])
        assert application.status == "withdrawn"


# --- review -----------------------------------------------------------------------------------


async def test_only_owner_can_review(
    ada: httpx.AsyncClient,
    grace: httpx.AsyncClient,
    eve: httpx.AsyncClient,
    frank: httpx.AsyncClient,
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    member_app = await apply(grace, pid)
    await act(ada, pid, member_app["id"], "accept")  # grace is now "another member"

    applicant_app = await apply(eve, pid)  # eve is the applicant under review
    for client in (eve, grace, frank):
        assert (await client.get(f"/api/v1/projects/{pid}/applications")).status_code == 403
        assert (await act(client, pid, applicant_app["id"], "accept")).status_code == 403
        assert (await act(client, pid, applicant_app["id"], "reject")).status_code == 403


async def test_accepting_adds_member_and_chat(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    project = await created_project(
        ada, capacity=5, roles=[{"title": "Dev", "open_slots": 2, "skills": []}]
    )
    pid, role_id = project["id"], project["roles"][0]["id"]
    applied = await apply(grace, pid, role_id)
    accepted = await act(ada, pid, applied["id"], "accept")
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["status"] == "accepted" and body["decided_at"] is not None
    assert body["role"]["id"] == role_id

    grace_id = await user_id(grace)
    row = await scalar(
        clean_database,
        "SELECT role_id::text FROM project_members WHERE project_id = :pid AND user_id = :uid",
        pid=pid,
        uid=grace_id,
    )
    assert row == role_id

    conv_id = (await grace.get(f"/api/v1/projects/{pid}")).json()["conversation_id"]
    assert conv_id is not None
    active = await scalar(
        clean_database,
        "SELECT active FROM conversation_members WHERE conversation_id = :cid AND user_id = :uid",
        cid=conv_id,
        uid=grace_id,
    )
    assert active is True


async def test_two_accepts_race_for_the_last_slot(
    ada: httpx.AsyncClient,
    grace: httpx.AsyncClient,
    eve: httpx.AsyncClient,
    clean_database: AsyncEngine,
) -> None:
    project = await created_project(ada, capacity=2, roles=[])
    pid = project["id"]
    app1 = await apply(grace, pid)
    app2 = await apply(eve, pid)

    responses = await asyncio.gather(
        act(ada, pid, app1["id"], "accept"), act(ada, pid, app2["id"], "accept")
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    error_codes = [r.json()["error"]["code"] for r in responses if r.status_code == 409]
    assert error_codes == ["capacity_reached"]

    status = await scalar(clean_database, "SELECT status FROM projects WHERE id = :pid", pid=pid)
    assert status == "full"
    pending = await scalar(
        clean_database,
        "SELECT count(*) FROM applications WHERE project_id = :pid AND status = 'pending'",
        pid=pid,
    )
    accepted = await scalar(
        clean_database,
        "SELECT count(*) FROM applications WHERE project_id = :pid AND status = 'accepted'",
        pid=pid,
    )
    assert (pending, accepted) == (1, 1)


async def test_accepting_past_capacity_is_refused_without_a_race(
    ada: httpx.AsyncClient,
    grace: httpx.AsyncClient,
    eve: httpx.AsyncClient,
    clean_database: AsyncEngine,
) -> None:
    """Capacity 2, two pending applications, accepted one after the other: the second must be
    refused once members == capacity, not only once members > capacity."""
    project = await created_project(ada, capacity=2, roles=[])
    pid = project["id"]
    app1 = await apply(grace, pid)
    app2 = await apply(eve, pid)

    first = await act(ada, pid, app1["id"], "accept")
    assert first.status_code == 200, first.text
    detail = (await ada.get(f"/api/v1/projects/{pid}")).json()
    assert detail["status"] == "full"
    assert detail["member_count"] == 2

    second = await act(ada, pid, app2["id"], "accept")
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "capacity_reached"

    pending_status = await scalar(
        clean_database, "SELECT status FROM applications WHERE id = :id", id=app2["id"]
    )
    assert pending_status == "pending"
    members = await scalar(
        clean_database, "SELECT count(*) FROM project_members WHERE project_id = :pid", pid=pid
    )
    assert members == 2


async def test_two_accepts_race_for_a_roles_last_slot(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, eve: httpx.AsyncClient
) -> None:
    project = await created_project(
        ada, capacity=5, roles=[{"title": "Dev", "open_slots": 1, "skills": []}]
    )
    pid, role_id = project["id"], project["roles"][0]["id"]
    app1 = await apply(grace, pid, role_id)
    app2 = await apply(eve, pid, role_id)

    responses = await asyncio.gather(
        act(ada, pid, app1["id"], "accept"), act(ada, pid, app2["id"], "accept")
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    error_codes = [r.json()["error"]["code"] for r in responses if r.status_code == 409]
    assert error_codes == ["role_full"]


async def test_accepting_twice_gives_409(ada: httpx.AsyncClient, grace: httpx.AsyncClient) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    applied = await apply(grace, pid)
    await act(ada, pid, applied["id"], "accept")
    again = await act(ada, pid, applied["id"], "accept")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "application_not_pending"


async def test_rejecting_then_repeat_actions_give_409(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    applied = await apply(grace, pid)
    rejected = await act(ada, pid, applied["id"], "reject")
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["decided_at"] is not None
    for action in ("accept", "reject"):
        response = await act(ada, pid, applied["id"], action)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "application_not_pending"


async def test_withdrawing(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, eve: httpx.AsyncClient
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    applied = await apply(grace, pid)
    for client in (ada, eve):
        assert (await act(client, pid, applied["id"], "withdraw")).status_code == 404
    assert (await act(grace, pid, applied["id"], "withdraw")).status_code == 204
    repeat = await act(grace, pid, applied["id"], "withdraw")
    assert repeat.status_code == 409
    assert repeat.json()["error"]["code"] == "application_not_pending"
    accept_withdrawn = await act(ada, pid, applied["id"], "accept")
    assert accept_withdrawn.status_code == 409
    assert accept_withdrawn.json()["error"]["code"] == "application_not_pending"


async def test_accepting_on_a_closed_project(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    applied = await apply(grace, pid)
    await ada.patch(f"/api/v1/projects/{pid}", json={"status": "closed"})
    response = await act(ada, pid, applied["id"], "accept")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "project_closed"


async def test_accepting_the_last_place_fills_the_project(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, eve: httpx.AsyncClient
) -> None:
    project = await created_project(ada, capacity=2, roles=[])
    pid = project["id"]
    applied = await apply(grace, pid)
    accepted = await act(ada, pid, applied["id"], "accept")
    assert accepted.status_code == 200

    detail = (await ada.get(f"/api/v1/projects/{pid}")).json()
    assert detail["status"] == "full"
    listed = (await eve.get("/api/v1/projects")).json()["items"]
    assert pid not in [p["id"] for p in listed]
    response = await eve.post(f"/api/v1/projects/{pid}/apply", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "project_full"


async def test_application_from_another_project_is_404(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    project1 = await created_project(ada, capacity=5, roles=[])
    project2 = await created_project(ada, capacity=5, roles=[])
    applied = await apply(grace, project1["id"])
    response = await act(ada, project2["id"], applied["id"], "accept")
    assert response.status_code == 404


async def test_queue_filters_by_status_and_my_applications_lists_mine(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, eve: httpx.AsyncClient
) -> None:
    project = await created_project(ada, title="Team Alpha", capacity=5, roles=[])
    pid = project["id"]
    app1 = await apply(grace, pid)
    app2 = await apply(eve, pid)
    await act(ada, pid, app1["id"], "reject")

    pending_queue = (
        await ada.get(f"/api/v1/projects/{pid}/applications", params={"status": "pending"})
    ).json()
    assert [a["id"] for a in pending_queue["items"]] == [app2["id"]]
    rejected_queue = (
        await ada.get(f"/api/v1/projects/{pid}/applications", params={"status": "rejected"})
    ).json()
    assert [a["id"] for a in rejected_queue["items"]] == [app1["id"]]
    all_queue = (await ada.get(f"/api/v1/projects/{pid}/applications")).json()
    assert [a["id"] for a in all_queue["items"]] == [app2["id"], app1["id"]]

    mine = (await grace.get("/api/v1/me/applications")).json()
    assert [(a["id"], a["project"]["title"], a["status"]) for a in mine["items"]] == [
        (app1["id"], "Team Alpha", "rejected")
    ]


async def test_queue_status_may_repeat_to_select_several(
    ada: httpx.AsyncClient,
    grace: httpx.AsyncClient,
    eve: httpx.AsyncClient,
    frank: httpx.AsyncClient,
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    app1 = await apply(grace, pid)
    app2 = await apply(eve, pid)
    app3 = await apply(frank, pid)
    await act(ada, pid, app1["id"], "reject")
    await act(eve, pid, app2["id"], "withdraw")  # only the applicant may withdraw their own

    response = await ada.get(
        f"/api/v1/projects/{pid}/applications",
        params=[("status", "rejected"), ("status", "withdrawn")],
    )
    assert response.status_code == 200, response.text
    assert {a["id"] for a in response.json()["items"]} == {app1["id"], app2["id"]}

    single = await ada.get(f"/api/v1/projects/{pid}/applications", params={"status": "pending"})
    assert [a["id"] for a in single.json()["items"]] == [app3["id"]]

    bogus = await ada.get(f"/api/v1/projects/{pid}/applications", params={"status": "bogus"})
    assert bogus.status_code == 422
    assert bogus.json()["error"]["code"] == "validation_error"


# --- edit -------------------------------------------------------------------------------------


async def test_only_owner_can_edit(ada: httpx.AsyncClient, grace: httpx.AsyncClient) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    response = await grace.patch(f"/api/v1/projects/{project['id']}", json={"title": "New title"})
    assert response.status_code == 403
    missing = await ada.patch(f"/api/v1/projects/{uuid.uuid4()}", json={"title": "x"})
    assert missing.status_code == 404


async def test_capacity_cannot_drop_below_team(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    applied = await apply(grace, pid)
    await act(ada, pid, applied["id"], "accept")
    response = await ada.patch(f"/api/v1/projects/{pid}", json={"capacity": 1})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "capacity_too_low"


async def test_status_can_only_be_open_or_closed(ada: httpx.AsyncClient) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    full_attempt = await ada.patch(f"/api/v1/projects/{pid}", json={"status": "full"})
    assert full_attempt.status_code == 422
    assert full_attempt.json()["error"]["code"] == "invalid_status"
    bogus = await ada.patch(f"/api/v1/projects/{pid}", json={"status": "bogus"})
    assert bogus.status_code == 422
    assert bogus.json()["error"]["code"] == "validation_error"


async def test_raising_capacity_reopens_a_full_project(ada: httpx.AsyncClient) -> None:
    project = await created_project(ada, capacity=1, roles=[])
    response = await ada.patch(f"/api/v1/projects/{project['id']}", json={"capacity": 3})
    assert response.json()["status"] == "open"


async def test_raising_capacity_with_explicit_close_stays_closed(ada: httpx.AsyncClient) -> None:
    project = await created_project(ada, capacity=1, roles=[])
    response = await ada.patch(
        f"/api/v1/projects/{project['id']}", json={"capacity": 3, "status": "closed"}
    )
    assert response.json()["status"] == "closed"


async def test_reopening_a_project_at_capacity_is_refused(ada: httpx.AsyncClient) -> None:
    project = await created_project(ada, capacity=1, roles=[])
    response = await ada.patch(f"/api/v1/projects/{project['id']}", json={"status": "open"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "capacity_reached"


async def test_lowering_capacity_to_team_size_makes_it_full(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    applied = await apply(grace, pid)
    await act(ada, pid, applied["id"], "accept")
    response = await ada.patch(f"/api/v1/projects/{pid}", json={"capacity": 2})
    assert response.json()["status"] == "full"


async def test_roles_can_be_edited_added_and_removed(ada: httpx.AsyncClient) -> None:
    project = await created_project(
        ada,
        capacity=5,
        roles=[
            {"title": "Keep", "open_slots": 2, "skills": ["python"]},
            {"title": "Drop", "open_slots": 1, "skills": []},
        ],
    )
    pid = project["id"]
    keep_id = next(r["id"] for r in project["roles"] if r["title"] == "Keep")
    response = await ada.patch(
        f"/api/v1/projects/{pid}",
        json={
            "roles": [
                {"id": keep_id, "title": "Kept", "open_slots": 3, "skills": ["java"]},
                {"title": "Added", "open_slots": 1, "skills": []},
            ]
        },
    )
    assert response.status_code == 200, response.text
    by_title = {r["title"]: r for r in response.json()["roles"]}
    assert set(by_title) == {"Kept", "Added"}
    assert by_title["Kept"]["open_slots"] == 3
    assert [s["slug"] for s in by_title["Kept"]["skills"]] == ["java"]


async def test_role_open_slots_cannot_drop_below_holders(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, eve: httpx.AsyncClient
) -> None:
    project = await created_project(
        ada, capacity=5, roles=[{"title": "Dev", "open_slots": 2, "skills": []}]
    )
    pid, role_id = project["id"], project["roles"][0]["id"]
    for applicant in (grace, eve):
        applied = await apply(applicant, pid, role_id)
        await act(ada, pid, applied["id"], "accept")
    response = await ada.patch(
        f"/api/v1/projects/{pid}",
        json={"roles": [{"id": role_id, "title": "Dev", "open_slots": 1, "skills": []}]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "role_open_slots_too_low"


async def test_role_with_a_holder_cannot_be_removed(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    project = await created_project(
        ada, capacity=5, roles=[{"title": "Dev", "open_slots": 1, "skills": []}]
    )
    pid, role_id = project["id"], project["roles"][0]["id"]
    applied = await apply(grace, pid, role_id)
    await act(ada, pid, applied["id"], "accept")
    response = await ada.patch(f"/api/v1/projects/{pid}", json={"roles": []})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "cannot_remove_role_in_use"


async def test_role_with_a_pending_application_cannot_be_removed(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    project = await created_project(
        ada, capacity=5, roles=[{"title": "Dev", "open_slots": 1, "skills": []}]
    )
    pid, role_id = project["id"], project["roles"][0]["id"]
    await apply(grace, pid, role_id)
    response = await ada.patch(f"/api/v1/projects/{pid}", json={"roles": []})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "cannot_remove_role_in_use"


async def test_role_id_from_another_project_is_refused_on_edit(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    other = await created_project(
        grace, capacity=5, roles=[{"title": "Dev", "open_slots": 1, "skills": []}]
    )
    response = await ada.patch(
        f"/api/v1/projects/{project['id']}",
        json={
            "roles": [
                {"id": other["roles"][0]["id"], "title": "Dev", "open_slots": 1, "skills": []}
            ]
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_role"


# --- blocks -----------------------------------------------------------------------------------


async def test_block_between_owner_and_applicant_withdraws_application(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    applied = await apply(grace, project["id"])
    await grace.post("/api/v1/blocks", json={"user_id": await user_id(ada)})
    status = await scalar(
        clean_database, "SELECT status FROM applications WHERE id = :id", id=applied["id"]
    )
    assert status == "withdrawn"


async def test_block_in_the_other_direction_also_withdraws_the_application(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    applied = await apply(grace, project["id"])
    await ada.post("/api/v1/blocks", json={"user_id": await user_id(grace)})
    status = await scalar(
        clean_database, "SELECT status FROM applications WHERE id = :id", id=applied["id"]
    )
    assert status == "withdrawn"


async def test_block_between_owner_and_member_removes_the_member(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    applied = await apply(grace, pid)
    await act(ada, pid, applied["id"], "accept")
    conv_id = (await grace.get(f"/api/v1/projects/{pid}")).json()["conversation_id"]
    grace_id = await user_id(grace)

    await ada.post("/api/v1/blocks", json={"user_id": grace_id})
    remaining = await scalar(
        clean_database,
        "SELECT count(*) FROM project_members WHERE project_id = :pid AND user_id = :uid",
        pid=pid,
        uid=grace_id,
    )
    assert remaining == 0
    active = await scalar(
        clean_database,
        "SELECT active FROM conversation_members WHERE conversation_id = :cid AND user_id = :uid",
        cid=conv_id,
        uid=grace_id,
    )
    assert active is False
    assert (await grace.get(f"/api/v1/conversations/{conv_id}")).status_code == 404


async def test_block_between_two_ordinary_members_leaves_the_team_alone(
    ada: httpx.AsyncClient,
    grace: httpx.AsyncClient,
    eve: httpx.AsyncClient,
    clean_database: AsyncEngine,
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    for applicant in (grace, eve):
        applied = await apply(applicant, pid)
        await act(ada, pid, applied["id"], "accept")

    await grace.post("/api/v1/blocks", json={"user_id": await user_id(eve)})
    count = await scalar(
        clean_database, "SELECT count(*) FROM project_members WHERE project_id = :pid", pid=pid
    )
    assert count == 3


async def test_pending_application_despite_a_block_cannot_be_accepted(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    await ada.post("/api/v1/blocks", json={"user_id": await user_id(grace)})
    application_id = str(uuid.uuid4())
    await execute(
        clean_database,
        "INSERT INTO applications (id, project_id, applicant_id) VALUES (:id, :pid, :uid)",
        id=application_id,
        pid=pid,
        uid=await user_id(grace),
    )
    response = await act(ada, pid, application_id, "accept")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "application_not_pending"


async def test_block_waits_for_an_in_flight_accept_before_checking_membership(
    ada: httpx.AsyncClient,
    grace: httpx.AsyncClient,
    engine: AsyncEngine,
    clean_database: AsyncEngine,
) -> None:
    """Simulates an accept that has locked the application and written (but not committed) the
    membership row: the block must wait for that commit and then see, and remove, the member --
    not decide membership first and miss someone who was still mid-accept."""
    grace_id = await user_id(grace)
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    applied = await apply(grace, pid)
    aid = applied["id"]

    async with locked_row(engine, "applications", aid, "UPDATE") as holder:
        await holder.execute(
            text(
                "INSERT INTO project_members (project_id, user_id, member_role, role_id) "
                "VALUES (:pid, :uid, 'member', NULL)"
            ),
            {"pid": pid, "uid": grace_id},
        )
        await holder.execute(
            text("UPDATE applications SET status = 'accepted', decided_at = now() WHERE id = :id"),
            {"id": aid},
        )
        task = asyncio.create_task(ada.post("/api/v1/blocks", json={"user_id": grace_id}))
        await asyncio.sleep(0.3)
        assert not task.done()
        await holder.commit()
    response = await task
    assert response.status_code == 204, response.text

    members = await scalar(
        clean_database,
        "SELECT count(*) FROM project_members WHERE project_id = :pid AND user_id = :uid",
        pid=pid,
        uid=grace_id,
    )
    assert members == 0


# --- locking ------------------------------------------------------------------------------------


async def test_apply_waits_for_a_project_row_locked_for_update(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    # A full project takes apply's early "project_full" exit, which never writes a row, so any
    # blocking here is entirely down to apply's own read of the project, not masked by the
    # foreign key check Postgres would otherwise run on the application insert.
    project = await created_project(ada, capacity=1, roles=[])
    pid = project["id"]
    async with locked_project(engine, pid, "UPDATE"):
        task = asyncio.create_task(grace.post(f"/api/v1/projects/{pid}/apply", json={}))
        await asyncio.sleep(0.3)
        assert not task.done()
    response = await task
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "project_full"


async def test_accept_waits_for_a_project_row_locked_for_share(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    applied = await apply(grace, pid)
    async with locked_project(engine, pid, "SHARE"):
        task = asyncio.create_task(act(ada, pid, applied["id"], "accept"))
        await asyncio.sleep(0.3)
        assert not task.done()
    response = await task
    assert response.status_code == 200, response.text


async def test_patch_waits_for_a_project_row_locked_for_share(
    ada: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    # An empty body changes nothing, so the only reason this could block is the lock taken
    # up front to read the project -- not a write at commit time (which would block anyway).
    project = await created_project(ada, capacity=5, roles=[])
    pid = project["id"]
    async with locked_project(engine, pid, "SHARE"):
        task = asyncio.create_task(ada.patch(f"/api/v1/projects/{pid}", json={}))
        await asyncio.sleep(0.3)
        assert not task.done()
    response = await task
    assert response.status_code == 200, response.text
