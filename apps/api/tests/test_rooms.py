import asyncio
import base64
import hashlib
import hmac
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.rate_limit import Limit
from app.main import create_app
from app.modules.rooms import service as room_service
from app.modules.rooms.turn import generate_turn_credential
from tests.conftest import MemoryMailer, make_settings
from tests.helpers import PROFILE, execute, make_project_chat, scalar, student, user_id

pytestmark = pytest.mark.usefixtures("clean_database")


async def with_profile(app: object, mailer: MemoryMailer, email: str) -> httpx.AsyncClient:
    client = await student(app, mailer, email)
    name = email.split("@")[0].title()
    response = await client.put("/api/v1/me/profile", json={**PROFILE, "display_name": name})
    assert response.status_code == 200, response.text
    return client


async def connect_direct(a: httpx.AsyncClient, b: httpx.AsyncClient) -> str:
    """Connects two already-profiled students and returns their direct conversation's id."""
    b_id = await user_id(b)
    sent = (await a.post("/api/v1/connections", json={"user_id": b_id})).json()
    accepted = (await b.post(f"/api/v1/connections/{sent['id']}/accept")).json()
    return str(accepted["conversation_id"])


def calls_registry(app: object) -> Any:
    return app.other_asgi_app.state.calls  # type: ignore[attr-defined]


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
async def frank(app: object, mailer: MemoryMailer) -> AsyncIterator[httpx.AsyncClient]:
    client = await with_profile(app, mailer, "frank@northeastern.edu")
    yield client
    await client.aclose()


# --- starting a call ------------------------------------------------------------------------


async def test_starting_a_call_creates_an_active_room_for_members(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    conversation_id = await connect_direct(ada, grace)
    response = await ada.post("/api/v1/rooms", json={"conversation_id": conversation_id})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["conversation_id"] == conversation_id
    assert body["room_state"] == "active"
    assert body["ended_at"] is None
    assert body["participants"] == []


async def test_a_second_start_returns_the_same_room(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    conversation_id = await connect_direct(ada, grace)
    first = await ada.post("/api/v1/rooms", json={"conversation_id": conversation_id})
    second = await grace.post("/api/v1/rooms", json={"conversation_id": conversation_id})
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


async def test_concurrent_starts_create_only_one_room(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    conversation_id = await connect_direct(ada, grace)
    responses = await asyncio.gather(
        *[grace.post("/api/v1/rooms", json={"conversation_id": conversation_id}) for _ in range(5)]
    )
    codes = [r.status_code for r in responses]
    assert codes.count(201) == 1
    assert codes.count(200) == 4
    assert len({r.json()["id"] for r in responses}) == 1
    count = await scalar(
        clean_database,
        "SELECT count(*) FROM rooms WHERE conversation_id = :cid",
        cid=conversation_id,
    )
    assert count == 1


async def test_starting_with_extra_fields_is_refused(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    conversation_id = await connect_direct(ada, grace)
    response = await ada.post(
        "/api/v1/rooms", json={"conversation_id": conversation_id, "surprise": "field"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_starting_is_rate_limited(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(room_service, "START_LIMIT", Limit(max_events=1, window_seconds=3600))
    conversation_id = await connect_direct(ada, grace)
    assert (
        await ada.post("/api/v1/rooms", json={"conversation_id": conversation_id})
    ).status_code == 201
    limited = await ada.post("/api/v1/rooms", json={"conversation_id": conversation_id})
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1


# --- access: non-members, blocked partners, removed teammates, missing ids -------------------


async def test_non_members_get_404_on_start_view_and_ice_servers(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, frank: httpx.AsyncClient
) -> None:
    conversation_id = await connect_direct(ada, grace)
    room = (await ada.post("/api/v1/rooms", json={"conversation_id": conversation_id})).json()

    assert (
        await frank.post("/api/v1/rooms", json={"conversation_id": conversation_id})
    ).status_code == 404
    assert (await frank.get(f"/api/v1/rooms/{room['id']}")).status_code == 404
    assert (await frank.get(f"/api/v1/rooms/{room['id']}/ice-servers")).status_code == 404


async def test_a_blocked_direct_partner_gets_404(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    conversation_id = await connect_direct(ada, grace)
    room = (await ada.post("/api/v1/rooms", json={"conversation_id": conversation_id})).json()
    grace_id = await user_id(grace)

    assert (await ada.post("/api/v1/blocks", json={"user_id": grace_id})).status_code == 204

    for client in (ada, grace):
        assert (await client.get(f"/api/v1/rooms/{room['id']}")).status_code == 404
        assert (await client.get(f"/api/v1/rooms/{room['id']}/ice-servers")).status_code == 404
        assert (
            await client.post("/api/v1/rooms", json={"conversation_id": conversation_id})
        ).status_code == 404


async def test_a_student_removed_from_the_team_gets_404(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    _, conversation_id = await make_project_chat(clean_database, ada_id, [grace_id])
    room = (await ada.post("/api/v1/rooms", json={"conversation_id": conversation_id})).json()

    await execute(
        clean_database,
        "UPDATE conversation_members SET active = false "
        "WHERE conversation_id = :cid AND user_id = :uid",
        cid=conversation_id,
        uid=grace_id,
    )

    assert (await grace.get(f"/api/v1/rooms/{room['id']}")).status_code == 404
    assert (await grace.get(f"/api/v1/rooms/{room['id']}/ice-servers")).status_code == 404
    assert (
        await grace.post("/api/v1/rooms", json={"conversation_id": conversation_id})
    ).status_code == 404
    # The owner, still an active member, keeps access.
    assert (await ada.get(f"/api/v1/rooms/{room['id']}")).status_code == 200


async def test_a_missing_room_id_is_404(ada: httpx.AsyncClient) -> None:
    missing = uuid.uuid4()
    assert (await ada.get(f"/api/v1/rooms/{missing}")).status_code == 404
    assert (await ada.get(f"/api/v1/rooms/{missing}/ice-servers")).status_code == 404


# --- what the room view shows ------------------------------------------------------------------


async def test_the_room_view_shows_kind_name_capacity_and_participants_for_a_direct_call(
    app: object, ada: httpx.AsyncClient, grace: httpx.AsyncClient
) -> None:
    conversation_id = await connect_direct(ada, grace)
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    room = (await ada.post("/api/v1/rooms", json={"conversation_id": conversation_id})).json()
    assert room["kind"] == "direct"
    assert room["name"] == "Grace"
    assert room["project_id"] is None
    assert room["capacity"] == 4

    registry = calls_registry(app)
    registry.add(uuid.UUID(room["id"]), uuid.UUID(ada_id), "sid-ada")
    registry.add(uuid.UUID(room["id"]), uuid.UUID(grace_id), "sid-grace")

    viewed = (await grace.get(f"/api/v1/rooms/{room['id']}")).json()
    assert viewed["name"] == "Ada"  # the other student, from grace's point of view
    assert viewed["participants"] == [
        {"user_id": ada_id, "display_name": "Ada"},
        {"user_id": grace_id, "display_name": "Grace"},
    ]


async def test_the_room_view_shows_the_project_title_and_id_for_a_team_call(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    ada_id, grace_id = await user_id(ada), await user_id(grace)
    project_id, conversation_id = await make_project_chat(
        clean_database, ada_id, [grace_id], title="Music Recommender"
    )
    room = (await ada.post("/api/v1/rooms", json={"conversation_id": conversation_id})).json()
    assert room["kind"] == "project"
    assert room["name"] == "Music Recommender"
    assert room["project_id"] == project_id


# --- ended rooms -----------------------------------------------------------------------------


async def test_an_ended_room_stays_viewable_refuses_ice_servers_and_restarting_makes_a_new_one(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    conversation_id = await connect_direct(ada, grace)
    room = (await ada.post("/api/v1/rooms", json={"conversation_id": conversation_id})).json()
    await execute(
        clean_database,
        "UPDATE rooms SET room_state = 'ended', ended_at = now() WHERE id = :id",
        id=room["id"],
    )

    viewed = await grace.get(f"/api/v1/rooms/{room['id']}")
    assert viewed.status_code == 200
    body = viewed.json()
    assert body["room_state"] == "ended"
    assert body["ended_at"] is not None

    ice = await grace.get(f"/api/v1/rooms/{room['id']}/ice-servers")
    assert ice.status_code == 409
    assert ice.json()["error"]["code"] == "room_ended"

    restarted = await ada.post("/api/v1/rooms", json={"conversation_id": conversation_id})
    assert restarted.status_code == 201
    assert restarted.json()["id"] != room["id"]
    assert restarted.json()["room_state"] == "active"


# --- ice servers -------------------------------------------------------------------------------


async def _direct_room(
    custom_app: object, mailer: MemoryMailer
) -> tuple[httpx.AsyncClient, httpx.AsyncClient, dict[str, Any]]:
    a = await with_profile(custom_app, mailer, "ada@northeastern.edu")
    b = await with_profile(custom_app, mailer, "grace@northeastern.edu")
    conversation_id = await connect_direct(a, b)
    room = (await a.post("/api/v1/rooms", json={"conversation_id": conversation_id})).json()
    return a, b, room


async def test_ice_servers_includes_stun_only_when_configured(mailer: MemoryMailer) -> None:
    custom = create_app(
        make_settings(stun_urls="stun:stun.example:19302,stun:stun2.example:19302"), mailer=mailer
    )
    ada, _, room = await _direct_room(custom, mailer)
    response = await ada.get(f"/api/v1/rooms/{room['id']}/ice-servers")
    assert response.status_code == 200
    body = response.json()
    assert body["ice_servers"] == [
        {"urls": ["stun:stun.example:19302", "stun:stun2.example:19302"]}
    ]
    assert body["expires_at"] is None


async def test_ice_servers_omits_turn_without_a_secret(mailer: MemoryMailer) -> None:
    custom = create_app(make_settings(turn_urls="turn:turn.example:3478"), mailer=mailer)
    ada, _, room = await _direct_room(custom, mailer)
    response = await ada.get(f"/api/v1/rooms/{room['id']}/ice-servers")
    assert response.status_code == 200
    body = response.json()
    assert body["ice_servers"] == []
    assert body["expires_at"] is None


async def test_ice_servers_includes_a_signed_turn_credential(mailer: MemoryMailer) -> None:
    secret = "s" * 40
    custom = create_app(
        make_settings(
            turn_urls="turn:turn.example:3478",
            turn_secret=secret,
            turn_credential_ttl_seconds=120,
        ),
        mailer=mailer,
    )
    ada, _, room = await _direct_room(custom, mailer)
    ada_id = await user_id(ada)

    before = datetime.now(UTC)
    response = await ada.get(f"/api/v1/rooms/{room['id']}/ice-servers")
    after = datetime.now(UTC)

    assert response.status_code == 200
    assert secret not in response.text
    body = response.json()
    turn_entry = next(entry for entry in body["ice_servers"] if "username" in entry)
    assert turn_entry["urls"] == ["turn:turn.example:3478"]

    expires_unix_str, user_id_part = turn_entry["username"].split(":", 1)
    assert user_id_part == ada_id
    expected_digest = hmac.new(
        secret.encode(), turn_entry["username"].encode(), hashlib.sha1
    ).digest()
    assert turn_entry["credential"] == base64.b64encode(expected_digest).decode()

    expires_at = datetime.fromisoformat(body["expires_at"])
    assert before + timedelta(seconds=119) <= expires_at <= after + timedelta(seconds=121)
    assert int(expires_unix_str) == int(expires_at.timestamp())


async def test_ice_servers_is_rate_limited(
    ada: httpx.AsyncClient, grace: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(room_service, "ICE_SERVERS_LIMIT", Limit(max_events=1, window_seconds=3600))
    conversation_id = await connect_direct(ada, grace)
    room = (await ada.post("/api/v1/rooms", json={"conversation_id": conversation_id})).json()
    assert (await ada.get(f"/api/v1/rooms/{room['id']}/ice-servers")).status_code == 200
    limited = await ada.get(f"/api/v1/rooms/{room['id']}/ice-servers")
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1


# --- turn.py: a pure function, tested without any HTTP ----------------------------------------


def test_turn_credential_matches_a_known_hmac_vector() -> None:
    secret = "topsecretvalue"
    user_id_ = uuid.UUID("11111111-1111-1111-1111-111111111111")
    now = datetime(2024, 1, 1, tzinfo=UTC)

    credential = generate_turn_credential(secret, user_id_, 60, now)

    expected_expiry = int(datetime(2024, 1, 1, 0, 1, tzinfo=UTC).timestamp())
    expected_username = f"{expected_expiry}:{user_id_}"
    expected_credential = base64.b64encode(
        hmac.new(secret.encode(), expected_username.encode(), hashlib.sha1).digest()
    ).decode()

    assert credential.username == expected_username
    assert credential.credential == expected_credential
    assert credential.expires_at == datetime(2024, 1, 1, 0, 1, tzinfo=UTC)


def test_turn_credential_expiry_is_now_plus_ttl() -> None:
    now = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
    credential = generate_turn_credential("some-secret", uuid.uuid4(), 3600, now)
    assert credential.expires_at == now + timedelta(seconds=3600)


def test_turn_credential_username_carries_the_expiry_and_the_user_id() -> None:
    user_id_ = uuid.uuid4()
    now = datetime(2025, 1, 1, tzinfo=UTC)
    credential = generate_turn_credential("some-secret", user_id_, 90, now)
    expires_unix, carried_user_id = credential.username.split(":", 1)
    assert int(expires_unix) == int((now + timedelta(seconds=90)).timestamp())
    assert carried_user_id == str(user_id_)
