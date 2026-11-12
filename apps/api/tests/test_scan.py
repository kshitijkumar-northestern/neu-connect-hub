import asyncio
import hashlib
import logging
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.core.rate_limit import Limit, RateLimiter
from app.modules.auth.models import User
from app.modules.profiles.models import Profile
from app.modules.scan import service as scan_service
from app.modules.scan.service import NOT_FOUND, ScanService
from tests.conftest import MemoryMailer, make_settings
from tests.helpers import PROFILE, execute, new_client, scalar, student, user_id

pytestmark = pytest.mark.usefixtures("clean_database")

TOKEN_FORMAT = re.compile(r"[A-Za-z0-9_-]{43}")


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


async def my_code(client: httpx.AsyncClient) -> httpx.Response:
    return await client.get("/api/v1/me/connection-code")


async def rotate(client: httpx.AsyncClient) -> httpx.Response:
    return await client.post("/api/v1/me/connection-code/rotate")


async def disable(client: httpx.AsyncClient) -> httpx.Response:
    return await client.request("DELETE", "/api/v1/me/connection-code")


async def resolve(client: httpx.AsyncClient, token: object) -> httpx.Response:
    return await client.post("/api/v1/connect/resolve", json={"token": token})


def token_of(code: dict[str, Any]) -> str:
    assert code["url"], code
    url = cast(str, code["url"])
    return url.rsplit("token=", 1)[1]


class _CollectingHandler(logging.Handler):
    """Captures records instead of writing them anywhere, so a test can inspect them directly."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def assert_generic_not_found(response: httpx.Response) -> None:
    assert response.status_code == 404, response.text
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["message"] == NOT_FOUND


# --- the code itself --------------------------------------------------------------------------


async def test_get_is_off_then_rotate_gives_active_and_get_repeats_it(
    ada: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    off = (await my_code(ada)).json()
    assert off == {"status": "off", "url": None, "expires_at": None, "shareable": True}

    rotated = await rotate(ada)
    assert rotated.status_code == 201, rotated.text
    code = rotated.json()
    assert code["status"] == "active"
    assert code["shareable"] is True
    assert code["expires_at"] is not None
    token = token_of(code)
    assert code["url"] == f"http://localhost:8080/connect#token={token}"
    assert TOKEN_FORMAT.fullmatch(token)

    assert (await my_code(ada)).json() == code

    user = await user_id(ada)
    token_hash = await scalar(
        clean_database,
        "SELECT token_hash FROM profile_share_tokens WHERE user_id = :id AND active",
        id=user,
    )
    assert token_hash == hashlib.sha256(token.encode()).hexdigest()
    row_count = await scalar(
        clean_database, "SELECT count(*) FROM profile_share_tokens WHERE user_id = :id", id=user
    )
    assert row_count == 1
    # the raw token is kept nowhere: only its hash is ever written to a column
    raw_in_a_column = await scalar(
        clean_database,
        "SELECT count(*) FROM profile_share_tokens WHERE user_id = :id AND token_hash = :token",
        id=user,
        token=token,
    )
    assert raw_in_a_column == 0


async def test_rotating_revokes_the_old_code_and_the_new_one_resolves(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    first = token_of((await rotate(ada)).json())
    assert (await resolve(grace, first)).status_code == 200

    second = token_of((await rotate(ada)).json())
    assert first != second
    assert_generic_not_found(await resolve(grace, first))
    assert (await resolve(grace, second)).status_code == 200


async def test_concurrent_rotations_leave_exactly_one_active_row(
    ada: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    """A second connection holds the caller's users row locked; both rotations must wait for it,
    not race the one-active-code index."""
    user = await user_id(ada)
    async with clean_database.connect() as holder:
        await holder.execute(text("SELECT id FROM users WHERE id = :id FOR UPDATE"), {"id": user})
        tasks = [asyncio.create_task(rotate(ada)) for _ in range(2)]
        await asyncio.sleep(0.2)
        assert not any(task.done() for task in tasks), "rotations ran without the row lock"
        await holder.commit()
        responses = await asyncio.gather(*tasks)

    assert [r.status_code for r in responses] == [201, 201]
    active = await scalar(
        clean_database,
        "SELECT count(*) FROM profile_share_tokens WHERE user_id = :id AND active",
        id=user,
    )
    assert active == 1


async def test_delete_turns_the_code_off_and_is_idempotent(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    token = token_of((await rotate(ada)).json())

    assert (await disable(ada)).status_code == 204
    assert_generic_not_found(await resolve(grace, token))
    assert (await my_code(ada)).json()["status"] == "off"
    assert (await disable(ada)).status_code == 204  # idempotent


async def test_an_expired_code_is_404_and_get_shows_expired(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    token = token_of((await rotate(ada)).json())
    await execute(
        clean_database,
        "UPDATE profile_share_tokens SET created_at = now() - interval '2 hours', "
        "expires_at = now() - interval '1 hour' WHERE user_id = :id AND active",
        id=await user_id(ada),
    )

    assert_generic_not_found(await resolve(grace, token))
    code = (await my_code(ada)).json()
    assert code["status"] == "expired"
    assert code["url"] is None
    assert code["expires_at"] is not None


async def test_my_code_prefers_the_active_row_even_if_a_revoked_row_is_newer(
    ada: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    """A race between overlapping rotations can leave a revoked row with a later created_at than
    the row that's actually active; picking "the newest" would report a live code as off."""
    active_code = (await rotate(ada)).json()
    user = await user_id(ada)
    await execute(
        clean_database,
        "INSERT INTO profile_share_tokens "
        "(id, user_id, token_hash, active, created_at, expires_at, revoked_at) "
        "VALUES (:id, :user, :hash, false, now() + interval '1 hour', "
        "now() + interval '2 hours', now())",
        id=str(uuid.uuid4()),
        user=user,
        hash="0" * 64,
    )

    assert (await my_code(ada)).json() == active_code


async def test_my_code_reports_off_when_the_signing_key_no_longer_matches(
    ada: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    await rotate(ada)
    monkeypatch.setattr(settings, "share_token_secret", SecretStr("a-totally-different-local-key"))

    assert (await my_code(ada)).json() == {
        "status": "off",
        "url": None,
        "expires_at": None,
        "shareable": True,
    }


# --- every failure short of success is the same 404 ------------------------------------------


async def test_malformed_missing_or_wrongly_typed_tokens_get_the_generic_404(
    grace: httpx.AsyncClient,
) -> None:
    for bad in ("short", "x" * 42, "x" * 44, "!" * 43, "y" * 43, None, 12345, ["a" * 43]):
        assert_generic_not_found(await resolve(grace, bad))

    # the key itself may be absent, not just its value
    assert_generic_not_found(await grace.post("/api/v1/connect/resolve", json={}))


async def test_resolve_refuses_unknown_fields(grace: httpx.AsyncClient) -> None:
    response = await grace.post(
        "/api/v1/connect/resolve", json={"token": "x" * 43, "extra": "nope"}
    )
    assert response.status_code == 422


async def test_the_old_get_resolve_route_is_gone(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    token = token_of((await rotate(ada)).json())
    assert (await grace.get(f"/api/v1/connect/resolve/{token}")).status_code == 404
    assert (await grace.get("/api/v1/connect/resolve")).status_code == 405


async def test_an_own_code_is_the_generic_404(ada: httpx.AsyncClient) -> None:
    token = token_of((await rotate(ada)).json())
    assert_generic_not_found(await resolve(ada, token))


async def test_a_revoked_code_is_the_generic_404(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    old = token_of((await rotate(ada)).json())
    await rotate(ada)
    assert_generic_not_found(await resolve(grace, old))


async def test_a_suspended_unverified_or_profile_less_owner_is_the_generic_404(
    app: object,
    mailer: MemoryMailer,
    ada: httpx.AsyncClient,
    grace: httpx.AsyncClient,
    clean_database: AsyncEngine,
) -> None:
    owner = await user_id(ada)
    token = token_of((await rotate(ada)).json())

    await execute(clean_database, "UPDATE users SET suspended_at = now() WHERE id = :id", id=owner)
    assert_generic_not_found(await resolve(grace, token))
    await execute(clean_database, "UPDATE users SET suspended_at = NULL WHERE id = :id", id=owner)

    await execute(clean_database, "UPDATE users SET verified_at = NULL WHERE id = :id", id=owner)
    assert_generic_not_found(await resolve(grace, token))
    await execute(clean_database, "UPDATE users SET verified_at = now() WHERE id = :id", id=owner)

    no_profile = await student(app, mailer, "noprofile@northeastern.edu")
    try:
        no_profile_token = token_of((await rotate(no_profile)).json())
        assert_generic_not_found(await resolve(grace, no_profile_token))
    finally:
        await no_profile.aclose()


async def test_a_hidden_or_blocked_owner_is_the_generic_404_but_connected_still_resolves(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, eve: httpx.AsyncClient
) -> None:
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    sent = (await grace.post("/api/v1/connections", json={"user_id": ada_id})).json()
    await ada.post(f"/api/v1/connections/{sent['id']}/accept")

    await ada.put(
        "/api/v1/me/profile", json={**PROFILE, "display_name": "Ada", "discoverable": False}
    )
    hidden_token = token_of((await rotate(ada)).json())

    # a third party, not connected to ada: hidden means invisible
    assert_generic_not_found(await resolve(eve, hidden_token))

    # connected to ada: a hidden profile still resolves
    connected = await resolve(grace, hidden_token)
    assert connected.status_code == 200, connected.text
    assert connected.json()["connection_state"] == "connected"

    # a block either way still 404s, even while connected
    await grace.post("/api/v1/blocks", json={"user_id": ada_id})
    assert_generic_not_found(await resolve(grace, hidden_token))

    await grace.delete(f"/api/v1/blocks/{ada_id}")
    await ada.post("/api/v1/blocks", json={"user_id": grace_id})
    assert_generic_not_found(await resolve(grace, hidden_token))


async def test_a_block_either_way_hides_a_discoverable_code_too(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    """Discoverable alone would let this resolve; only the block check stops it."""
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    token = token_of((await rotate(ada)).json())
    assert (await resolve(grace, token)).status_code == 200

    await grace.post("/api/v1/blocks", json={"user_id": ada_id})
    assert_generic_not_found(await resolve(grace, token))
    await grace.delete(f"/api/v1/blocks/{ada_id}")
    assert (await resolve(grace, token)).status_code == 200

    await ada.post("/api/v1/blocks", json={"user_id": grace_id})
    assert_generic_not_found(await resolve(grace, token))


# --- authentication ----------------------------------------------------------------------------


async def test_anonymous_requests_are_rejected_on_every_route(
    app: object, ada: httpx.AsyncClient
) -> None:
    token = token_of((await rotate(ada)).json())
    async with new_client(app) as anonymous:
        assert (await my_code(anonymous)).status_code == 401
        assert (await anonymous.post("/api/v1/me/connection-code/rotate")).status_code == 401
        assert (await anonymous.request("DELETE", "/api/v1/me/connection-code")).status_code == 401
        assert (await resolve(anonymous, token)).status_code == 401


async def test_unverified_requests_are_forbidden_on_every_route(
    app: object, mailer: MemoryMailer, ada: httpx.AsyncClient
) -> None:
    token = token_of((await rotate(ada)).json())
    unverified = await student(app, mailer, "new@northeastern.edu", verified=False)
    try:
        assert (await my_code(unverified)).status_code == 403
        assert (await rotate(unverified)).status_code == 403
        assert (await disable(unverified)).status_code == 403
        assert (await resolve(unverified, token)).status_code == 403
    finally:
        await unverified.aclose()


# --- the preview ---------------------------------------------------------------------------


async def test_the_preview_carries_exactly_four_fields(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    token = token_of((await rotate(ada)).json())
    body = (await resolve(grace, token)).json()
    assert set(body) == {"user_id", "display_name", "headline", "connection_state"}
    assert body["display_name"] == "Ada"
    assert body["connection_state"] == "none"


async def test_connection_state_tracks_each_state_from_the_viewers_side(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, eve: httpx.AsyncClient
) -> None:
    ada_id = await user_id(ada)
    token = token_of((await rotate(ada)).json())

    assert (await resolve(grace, token)).json()["connection_state"] == "none"

    sent = (await grace.post("/api/v1/connections", json={"user_id": ada_id})).json()
    assert (await resolve(grace, token)).json()["connection_state"] == "pending_outgoing"

    eve_token = token_of((await rotate(eve)).json())
    await eve.post("/api/v1/connections", json={"user_id": ada_id})
    assert (await resolve(ada, eve_token)).json()["connection_state"] == "pending_incoming"

    await ada.post(f"/api/v1/connections/{sent['id']}/accept")
    assert (await resolve(grace, token)).json()["connection_state"] == "connected"


async def test_shareable_is_false_without_a_profile_or_when_hidden(
    app: object, mailer: MemoryMailer
) -> None:
    client = await student(app, mailer, "noprofile2@northeastern.edu")
    try:
        assert (await my_code(client)).json()["shareable"] is False

        await client.put(
            "/api/v1/me/profile", json={**PROFILE, "display_name": "Hidden", "discoverable": False}
        )
        assert (await my_code(client)).json()["shareable"] is False

        await client.put(
            "/api/v1/me/profile", json={**PROFILE, "display_name": "Hidden", "discoverable": True}
        )
        assert (await my_code(client)).json()["shareable"] is True
    finally:
        await client.aclose()


async def test_shareable_is_false_when_the_owner_is_unverified_or_suspended(
    db_session: AsyncSession,
) -> None:
    """Suspending or unverifying an account also ends its own sessions (see
    `auth/sessions.py`), so a student can never observe this through their own
    `/me/connection-code` call; it exercises the service's definition directly, the same one
    `resolve` relies on through `visible_to` for a stranger's scan."""
    owner = User(email="owner-for-shareable@northeastern.edu", password_hash="x", verified_at=None)
    db_session.add(owner)
    await db_session.flush()
    db_session.add(
        Profile(
            user_id=owner.id,
            display_name="Owner",
            timezone="America/New_York",
            discoverable=True,
        )
    )
    await db_session.flush()
    service = ScanService(db_session, make_settings(), RateLimiter())

    # unverified
    assert (await service.my_code(owner.id)).shareable is False

    # verified but suspended
    owner.verified_at = datetime.now(UTC)
    owner.suspended_at = datetime.now(UTC)
    await db_session.flush()
    assert (await service.my_code(owner.id)).shareable is False

    # verified and not suspended
    owner.suspended_at = None
    await db_session.flush()
    assert (await service.my_code(owner.id)).shareable is True


# --- rate limits and logging -------------------------------------------------------------------


async def test_rotate_is_rate_limited(
    ada: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        scan_service, "ROTATE_PER_STUDENT", Limit(max_events=2, window_seconds=3600)
    )
    assert (await rotate(ada)).status_code == 201
    assert (await rotate(ada)).status_code == 201
    limited = await rotate(ada)
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers


async def test_resolve_is_rate_limited(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        scan_service, "RESOLVE_PER_STUDENT", Limit(max_events=2, window_seconds=600)
    )
    token = token_of((await rotate(ada)).json())
    assert (await resolve(grace, token)).status_code == 200
    assert_generic_not_found(await resolve(grace, "z" * 43))
    limited = await resolve(grace, token)
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers


async def test_the_raw_token_never_reaches_the_access_log(
    live_server: str, mailer: MemoryMailer
) -> None:
    """The token travels only in the JSON body of POST /connect/resolve, never in a URL, so a
    plain access-log handler (standing in for every proxy and tracer in front of the API) never
    sees it."""
    owner = await with_profile(live_server, mailer, "ada@northeastern.edu")
    try:
        token = token_of((await rotate(owner)).json())

        access_logger = logging.getLogger("uvicorn.access")
        previous_level = access_logger.level
        handler = _CollectingHandler()
        access_logger.addHandler(handler)
        access_logger.setLevel(logging.INFO)
        viewer = await student(live_server, mailer, "grace@northeastern.edu")
        try:
            response = await viewer.post("/api/v1/connect/resolve", json={"token": token})
            assert response.status_code == 200, response.text
        finally:
            await viewer.aclose()
            access_logger.removeHandler(handler)
            access_logger.setLevel(previous_level)

        assert handler.records, "no access log records were captured"
        logged = "\n".join(record.getMessage() for record in handler.records)
        assert token not in logged
    finally:
        await owner.aclose()
