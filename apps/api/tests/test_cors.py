"""Cross-origin access for the split deployment (web on the site's host, API on a sibling host):
only the web origin may read API responses with credentials, and nothing else changes for anyone
else, so the Origin and CSRF checks still refuse their unsafe requests."""

from typing import Any

import httpx
import pytest
from starlette.middleware.cors import CORSMiddleware

from app.core.security import CSRF_HEADER
from app.main import create_app
from tests.conftest import WEB_ORIGIN, MemoryMailer, make_settings, production_settings
from tests.helpers import csrf, profile, student

pytestmark = pytest.mark.usefixtures("clean_database")

FOREIGN_ORIGINS = [
    "https://evil.example",
    # Look-alikes of the allowed origin: a prefix match or a regex would let these through.
    f"{WEB_ORIGIN}.evil.example",
    "http://localhost:8081",
    "https://localhost:8080",
    "null",
]


def cors_headers(response: httpx.Response) -> dict[str, str]:
    return {k: v for k, v in response.headers.items() if k.lower().startswith("access-control-")}


def preflight_headers(origin: str, method: str = "PUT") -> dict[str, str]:
    return {
        "Origin": origin,
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": f"content-type, {CSRF_HEADER.lower()}",
    }


def anonymous(app: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    )


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
async def test_a_preflight_from_the_web_origin_is_allowed(app: object, method: str) -> None:
    async with anonymous(app) as client:
        response = await client.options(
            "/api/v1/me/profile", headers=preflight_headers(WEB_ORIGIN, method)
        )

    assert response.status_code == 200
    headers = cors_headers(response)
    assert headers["access-control-allow-origin"] == WEB_ORIGIN
    assert headers["access-control-allow-credentials"] == "true"
    assert method in headers["access-control-allow-methods"].split(", ")
    allowed = {h.strip().lower() for h in headers["access-control-allow-headers"].split(",")}
    assert {"content-type", CSRF_HEADER.lower()} <= allowed


async def test_the_preflight_allows_only_the_listed_methods_and_headers(app: object) -> None:
    async with anonymous(app) as client:
        allowed = await client.options("/api/v1/me/profile", headers=preflight_headers(WEB_ORIGIN))
        odd_header = await client.options(
            "/api/v1/me/profile",
            headers={**preflight_headers(WEB_ORIGIN), "Access-Control-Request-Headers": "x-evil"},
        )
        odd_method = await client.options(
            "/api/v1/me/profile", headers=preflight_headers(WEB_ORIGIN, "TRACE")
        )

    methods = set(cors_headers(allowed)["access-control-allow-methods"].split(", "))
    assert methods == {"GET", "POST", "PUT", "PATCH", "DELETE"}
    assert odd_header.status_code == 400
    assert odd_method.status_code == 400


@pytest.mark.parametrize("origin", FOREIGN_ORIGINS)
async def test_a_preflight_from_any_other_origin_gets_no_cors_headers(
    app: object, origin: str
) -> None:
    async with anonymous(app) as client:
        response = await client.options("/api/v1/me/profile", headers=preflight_headers(origin))

    assert cors_headers(response) == {}
    assert response.status_code >= 400


async def test_the_web_origin_may_read_a_credentialed_response(
    app: object, mailer: MemoryMailer
) -> None:
    client = await student(app, mailer, "ada@northeastern.edu")
    try:
        response = await client.get("/api/v1/me")
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == WEB_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "Origin" in response.headers["vary"]
    # The web client falls back to this header for an error without the JSON envelope.
    assert response.headers["access-control-expose-headers"] == "X-Request-ID"


@pytest.mark.parametrize("origin", FOREIGN_ORIGINS)
async def test_a_credentialed_request_from_any_other_origin_gets_no_cors_headers(
    app: object, mailer: MemoryMailer, origin: str
) -> None:
    client = await student(app, mailer, "ada@northeastern.edu")
    try:
        response = await client.get("/api/v1/me", headers={"Origin": origin})
    finally:
        await client.aclose()

    assert cors_headers(response) == {}
    # Without CORS headers the browser never lets that page read the body; caches must not
    # hand this answer to the web origin either.
    assert "Origin" in response.headers["vary"]


@pytest.mark.parametrize("origin", FOREIGN_ORIGINS)
async def test_the_origin_and_csrf_checks_still_refuse_unsafe_foreign_requests(
    app: object, mailer: MemoryMailer, origin: str
) -> None:
    client = await student(app, mailer, "ada@northeastern.edu")
    try:
        # Even with a valid session cookie and the matching CSRF token.
        response = await client.put(
            "/api/v1/me/profile", json=profile(), headers={"Origin": origin, **csrf(client)}
        )
        saved = await client.get("/api/v1/me/profile")
    finally:
        await client.aclose()

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"
    assert cors_headers(response) == {}
    assert saved.status_code == 404  # nothing was written


async def test_errors_for_the_web_origin_stay_readable(app: object) -> None:
    async with anonymous(app) as client:
        response = await client.get("/api/v1/me", headers={"Origin": WEB_ORIGIN})

    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == WEB_ORIGIN


async def test_the_split_production_origin_is_the_only_one_allowed() -> None:
    app = create_app(production_settings())
    async with anonymous(app) as client:
        web = await client.options(
            "/api/v1/me/profile", headers=preflight_headers("https://hub.example")
        )
        api_host = await client.options(
            "/api/v1/me/profile", headers=preflight_headers("https://api.hub.example")
        )
        plain_http = await client.options(
            "/api/v1/me/profile", headers=preflight_headers("http://hub.example")
        )

    assert web.headers["access-control-allow-origin"] == "https://hub.example"
    assert cors_headers(api_host) == {}
    assert cors_headers(plain_http) == {}


def test_the_allow_list_is_the_configured_web_origin_and_nothing_wider() -> None:
    """The CORS middleware is never configured with a wildcard or a pattern."""
    sio_app: Any = create_app(make_settings()).app  # the headers layer wraps Socket.IO
    http_app = sio_app.other_asgi_app
    options = next(m.kwargs for m in http_app.user_middleware if issubclass(m.cls, CORSMiddleware))

    assert options["allow_origins"] == [WEB_ORIGIN]
    assert options.get("allow_origin_regex") is None
    assert options["allow_credentials"] is True
    assert "*" not in options["allow_methods"]
    assert "*" not in options["allow_headers"]
