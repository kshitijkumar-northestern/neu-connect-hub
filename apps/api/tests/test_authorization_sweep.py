"""Sweeps across every HTTP route, read from the app's own OpenAPI schema so a new route is
covered the day it's added: unsafe requests from another origin or without the CSRF token are
refused, routes past sign-in need a verified email, and the admin area is hidden from students
and closed to administrators without a recent MFA check."""

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.security import CSRF_HEADER, UNSAFE_METHODS
from app.main import create_http_app
from tests.conftest import MemoryMailer, make_settings
from tests.helpers import block, make_admin, student, user_id
from tests.test_route_access import PUBLIC_ROUTES

PLACEHOLDER_ID = "00000000-0000-0000-0000-000000000000"
ADMIN_PREFIX = "/api/v1/admin/"
ADMIN_MFA_PREFIX = "/api/v1/admin/mfa"
# Signed in but not yet verified, a student may still see and delete their own account.
UNVERIFIED_ALLOWED = PUBLIC_ROUTES | {("GET", "/api/v1/me"), ("DELETE", "/api/v1/me")}


def _concrete(path: str) -> str:
    while "{" in path:
        start = path.index("{")
        path = path[:start] + PLACEHOLDER_ID + path[path.index("}", start) + 1 :]
    return path


def _routes() -> list[tuple[str, str]]:
    """Every (method, path template) the API serves."""
    schema = create_http_app(make_settings()).openapi()
    return sorted({(m.upper(), p) for p, methods in schema["paths"].items() for m in methods})


ALL = _routes()
UNSAFE = [(m, p) for m, p in ALL if m in UNSAFE_METHODS]
UNSAFE_PROTECTED = [(m, p) for m, p in UNSAFE if (m, p) not in PUBLIC_ROUTES]
VERIFIED_ONLY = [(m, p) for m, p in ALL if (m, p) not in UNVERIFIED_ALLOWED]
ADMIN = [(m, p) for m, p in ALL if p.startswith(ADMIN_PREFIX)]
ADMIN_PAST_MFA = [(m, p) for m, p in ADMIN if not p.startswith(ADMIN_MFA_PREFIX)]

# Canaries: an empty list would make its sweep pass with no cases at all.
assert len(UNSAFE_PROTECTED) >= 20, UNSAFE_PROTECTED
assert len(VERIFIED_ONLY) >= 30, VERIFIED_ONLY
assert len(ADMIN) >= 8 and len(ADMIN_PAST_MFA) >= 4, ADMIN


def ids(routes: list[tuple[str, str]]) -> list[str]:
    return [f"{m}_{p}" for m, p in routes]


def anonymous(app: object, origin: str | None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
        headers={"Origin": origin} if origin is not None else {},
    )


@pytest.mark.parametrize("origin", ["https://evil.example", None], ids=["foreign", "absent"])
@pytest.mark.parametrize(("method", "template"), UNSAFE, ids=ids(UNSAFE))
async def test_every_unsafe_route_refuses_another_origin(
    app: object, method: str, template: str, origin: str | None
) -> None:
    async with anonymous(app, origin) as client:
        response = await client.request(method, _concrete(template))

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "origin_not_allowed"


@pytest.mark.usefixtures("clean_database")
@pytest.mark.parametrize("token", [None, "forged-token-value"], ids=["missing", "forged"])
@pytest.mark.parametrize(("method", "template"), UNSAFE_PROTECTED, ids=ids(UNSAFE_PROTECTED))
async def test_every_signed_in_unsafe_route_needs_the_csrf_token(
    app: object, mailer: MemoryMailer, method: str, template: str, token: str | None
) -> None:
    client = await student(app, mailer, "ada@northeastern.edu")
    try:
        client.headers.pop(CSRF_HEADER)
        headers = {CSRF_HEADER: token} if token is not None else {}
        response = await client.request(method, _concrete(template), headers=headers)
    finally:
        await client.aclose()

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "csrf_failed"


@pytest.mark.usefixtures("clean_database")
@pytest.mark.parametrize(("method", "template"), VERIFIED_ONLY, ids=ids(VERIFIED_ONLY))
async def test_every_route_past_sign_in_needs_a_verified_email(
    app: object, mailer: MemoryMailer, method: str, template: str
) -> None:
    client = await student(app, mailer, "ada@northeastern.edu", verified=False)
    try:
        response = await client.request(method, _concrete(template))
    finally:
        await client.aclose()

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "verification_required"


@pytest.mark.usefixtures("clean_database")
@pytest.mark.parametrize(("method", "template"), ADMIN, ids=ids(ADMIN))
async def test_every_admin_route_looks_missing_to_a_student(
    app: object, mailer: MemoryMailer, method: str, template: str
) -> None:
    client = await student(app, mailer, "ada@northeastern.edu")
    try:
        response = await client.request(method, _concrete(template))
    finally:
        await client.aclose()

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(("method", "template"), ADMIN_PAST_MFA, ids=ids(ADMIN_PAST_MFA))
async def test_every_admin_route_past_mfa_needs_a_recent_mfa_check(
    app: object,
    mailer: MemoryMailer,
    clean_database: AsyncEngine,
    method: str,
    template: str,
) -> None:
    client = await student(app, mailer, "root@northeastern.edu")
    try:
        await make_admin(clean_database, await user_id(client))
        response = await client.request(method, _concrete(template))
    finally:
        await client.aclose()

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "mfa_required"


async def test_a_third_party_cannot_lift_someone_elses_block(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await student(app, mailer, "ada@northeastern.edu")
    grace = await student(app, mailer, "grace@northeastern.edu")
    eve = await student(app, mailer, "eve@northeastern.edu")
    try:
        grace_id = await user_id(grace)
        await block(ada, grace_id)

        # Unblocking only ever removes the caller's own block, so this is a no-op for ada's.
        assert (await eve.delete(f"/api/v1/blocks/{grace_id}")).status_code == 204
        listed = (await ada.get("/api/v1/blocks")).json()
    finally:
        for client in (ada, grace, eve):
            await client.aclose()

    assert [entry["user_id"] for entry in listed] == [grace_id]
