import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.profiles import trust
from app.modules.profiles.trust import REPORTS_PER_USER
from tests.conftest import MemoryMailer
from tests.helpers import PROFILE, scalar, student, user_id

pytestmark = pytest.mark.usefixtures("clean_database")


@pytest.fixture
async def ada(app: object, mailer: MemoryMailer) -> AsyncIterator[httpx.AsyncClient]:
    client = await student(app, mailer, "ada@northeastern.edu")
    yield client
    await client.aclose()


@pytest.fixture
async def grace(app: object, mailer: MemoryMailer) -> AsyncIterator[httpx.AsyncClient]:
    client = await student(app, mailer, "grace@northeastern.edu")
    await client.put("/api/v1/me/profile", json={**PROFILE, "display_name": "Grace Hopper"})
    yield client
    await client.aclose()


# --- blocking -------------------------------------------------------------------------------


async def test_blocking_and_unblocking_are_idempotent(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    grace_id = await user_id(grace)

    for _ in range(2):
        assert (await ada.post("/api/v1/blocks", json={"user_id": grace_id})).status_code == 204
    blocks = (await ada.get("/api/v1/blocks")).json()
    assert [(b["user_id"], b["display_name"]) for b in blocks] == [(grace_id, "Grace Hopper")]
    assert "grace@northeastern.edu" not in str(blocks)

    for _ in range(2):
        assert (await ada.delete(f"/api/v1/blocks/{grace_id}")).status_code == 204
    assert (await ada.get("/api/v1/blocks")).json() == []


async def test_the_block_list_is_capped_newest_first(
    app: object, mailer: MemoryMailer, ada: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trust, "MAX_LISTED_BLOCKS", 2)
    targets = []
    for name in ("one", "two", "three"):
        other = await student(app, mailer, f"{name}@northeastern.edu")
        targets.append(await user_id(other))
        await other.aclose()
        await ada.post("/api/v1/blocks", json={"user_id": targets[-1]})

    listed = [b["user_id"] for b in (await ada.get("/api/v1/blocks")).json()]
    assert listed == [targets[2], targets[1]]


async def test_a_block_list_is_private_to_its_owner(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    await ada.post("/api/v1/blocks", json={"user_id": await user_id(grace)})
    assert (await grace.get("/api/v1/blocks")).json() == []


async def test_students_cannot_block_themselves(ada: httpx.AsyncClient) -> None:
    response = await ada.post("/api/v1/blocks", json={"user_id": await user_id(ada)})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "cannot_target_self"


async def test_blocking_an_unknown_account_is_not_found(ada: httpx.AsyncClient) -> None:
    response = await ada.post("/api/v1/blocks", json={"user_id": str(uuid.uuid4())})
    assert response.status_code == 404


async def test_unverified_students_cannot_block_or_report(
    app: object, mailer: MemoryMailer, grace: httpx.AsyncClient
) -> None:
    client = await student(app, mailer, "new@northeastern.edu", verified=False)
    target = await user_id(grace)
    try:
        for path, body in [
            ("/api/v1/blocks", {"user_id": target}),
            ("/api/v1/reports", {"user_id": target, "reason": "spam"}),
        ]:
            assert (await client.post(path, json=body)).status_code == 403
    finally:
        await client.aclose()


# --- reporting ------------------------------------------------------------------------------


async def test_a_report_is_recorded_for_review(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    grace_id = await user_id(grace)
    response = await ada.post(
        "/api/v1/reports",
        json={"user_id": grace_id, "reason": "harassment", "details": "Repeated rude messages."},
    )
    assert response.status_code == 202
    assert response.json() == {"status": "report_received"}

    row = await scalar(
        clean_database,
        "SELECT reason || '|' || status || '|' || details FROM reports "
        "WHERE reporter_id = :me AND target_user_id = :target",
        me=await user_id(ada),
        target=grace_id,
    )
    assert row == "harassment|open|Repeated rude messages."


async def test_repeating_an_open_report_does_not_duplicate_it(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    body = {"user_id": await user_id(grace), "reason": "spam"}
    for _ in range(3):
        assert (await ada.post("/api/v1/reports", json=body)).status_code == 202
    assert await scalar(clean_database, "SELECT count(*) FROM reports") == 1


@pytest.mark.parametrize(
    "changes",
    [{"reason": "bad vibes"}, {"details": "x" * 1001}, {"reason": None}, {"extra": True}],
    ids=["unknown reason", "long details", "missing reason", "unknown field"],
)
async def test_invalid_reports_are_rejected(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, changes: dict[str, object]
) -> None:
    body = {"user_id": await user_id(grace), "reason": "spam", **changes}
    body = {key: value for key, value in body.items() if value is not None}
    assert (await ada.post("/api/v1/reports", json=body)).status_code == 422


async def test_students_cannot_report_themselves_or_unknown_accounts(
    ada: httpx.AsyncClient,
) -> None:
    own = await ada.post("/api/v1/reports", json={"user_id": await user_id(ada), "reason": "spam"})
    assert own.status_code == 422
    assert own.json()["error"]["code"] == "cannot_target_self"
    unknown = await ada.post(
        "/api/v1/reports", json={"user_id": str(uuid.uuid4()), "reason": "spam"}
    )
    assert unknown.status_code == 404


async def test_reports_are_rate_limited(ada: httpx.AsyncClient, grace: httpx.AsyncClient) -> None:
    body = {"user_id": await user_id(grace), "reason": "spam"}
    for _ in range(REPORTS_PER_USER.max_events):
        assert (await ada.post("/api/v1/reports", json=body)).status_code == 202
    limited = await ada.post("/api/v1/reports", json=body)
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1
