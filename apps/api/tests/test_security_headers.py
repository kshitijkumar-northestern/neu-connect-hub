"""Every HTTP response the API sends carries the same security headers, whatever its status."""

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.core.config import Settings
from app.core.headers import SecurityHeadersMiddleware
from app.core.security import WebOriginCORSMiddleware
from app.main import create_app
from tests.conftest import WEB_ORIGIN, make_settings, production_settings
from tests.helpers import new_client

# The API answers with JSON only, so nothing it serves may load, embed or frame anything.
EXPECTED = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
    "cross-origin-resource-policy": "same-site",
}
HSTS = "max-age=31536000; includeSubDomains"


def client_for(app: object, *, raise_app_exceptions: bool = True) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(
        app=app,  # type: ignore[arg-type]
        raise_app_exceptions=raise_app_exceptions,
    )
    return httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"Origin": WEB_ORIGIN}
    )


def http_app_of(app: Any) -> FastAPI:
    """The FastAPI app inside the composed one: headers layer, then Socket.IO, then HTTP."""
    http_app: FastAPI = app.app.other_asgi_app
    return http_app


def assert_security_headers(response: httpx.Response) -> None:
    for name, value in EXPECTED.items():
        assert response.headers.get(name) == value, (name, response.status_code)


def assert_not_cached(response: httpx.Response) -> None:
    assert response.headers.get("cache-control") == "no-store", response.status_code


async def test_a_successful_response_carries_every_header(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert_security_headers(response)
    assert_not_cached(response)


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/api/v1/me", 401),
        ("GET", "/api/v1/nope", 404),
        ("POST", "/api/v1/health", 405),
        ("POST", "/api/v1/auth/login", 422),
    ],
)
async def test_client_errors_carry_every_header(
    client: httpx.AsyncClient, method: str, path: str, status: int
) -> None:
    response = await client.request(method, path, json={})

    assert response.status_code == status
    assert_security_headers(response)
    assert_not_cached(response)


async def test_a_foreign_origin_refusal_carries_every_header(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={}, headers={"Origin": "https://evil.example"}
    )

    assert response.status_code == 403
    assert_security_headers(response)


async def test_a_body_refused_as_too_large_carries_every_header() -> None:
    async with new_client(create_app(make_settings(max_request_body_bytes=1024))) as client:
        response = await client.post(
            "/api/v1/auth/login",
            content=b"x" * 2048,
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 413
    assert_security_headers(response)
    assert_not_cached(response)


async def test_an_unhandled_error_carries_every_header(settings: Settings) -> None:
    app = create_app(settings)

    @http_app_of(app).get("/api/v1/_boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    async with client_for(app) as client:
        response = await client.get("/api/v1/_boom")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert_security_headers(response)
    assert_not_cached(response)


async def test_an_error_the_request_context_never_sees_carries_every_header(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Starlette's own outermost handler answers this 500 (here the CORS layer itself fails),
    outside every middleware the HTTP app adds."""

    def broken(self: WebOriginCORSMiddleware, origin: str) -> bool:
        raise RuntimeError("broken")

    monkeypatch.setattr(WebOriginCORSMiddleware, "is_allowed_origin", broken)
    async with client_for(create_app(settings), raise_app_exceptions=False) as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 500
    assert_security_headers(response)
    assert_not_cached(response)


def test_one_layer_around_everything_sets_the_headers(settings: Settings) -> None:
    app = create_app(settings)

    assert isinstance(app, SecurityHeadersMiddleware)
    inner: list[object] = [m.cls for m in http_app_of(app).user_middleware]
    assert SecurityHeadersMiddleware not in inner


async def test_a_route_cannot_loosen_the_headers(settings: Settings) -> None:
    app = create_app(settings)

    @http_app_of(app).get("/api/v1/_framed")
    async def framed() -> JSONResponse:
        return JSONResponse(
            {}, headers={"X-Frame-Options": "SAMEORIGIN", "Cache-Control": "max-age=3600"}
        )

    async with client_for(app) as client:
        response = await client.get("/api/v1/_framed")

    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"


async def test_paths_outside_the_api_carry_the_headers_too(client: httpx.AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 404
    assert_security_headers(response)


@pytest.mark.parametrize("origin", [WEB_ORIGIN, "https://evil.example"])
async def test_socket_io_long_polling_responses_carry_the_headers_too(
    settings: Settings, origin: str
) -> None:
    """Engine.IO answers /socket.io/ itself, never reaching FastAPI's middleware."""
    async with client_for(create_app(settings)) as client:
        response = await client.get(
            "/socket.io/", params={"EIO": "4", "transport": "polling"}, headers={"Origin": origin}
        )

    assert response.status_code == (200 if origin == WEB_ORIGIN else 400)
    assert_security_headers(response)
    assert response.headers["cache-control"] == "no-store"


async def test_hsts_is_sent_in_production() -> None:
    async with client_for(create_app(production_settings())) as client:
        ok = await client.get("/api/v1/health")
        missing = await client.get("/api/v1/nope")

    assert ok.headers["strict-transport-security"] == HSTS
    assert missing.headers["strict-transport-security"] == HSTS
    assert_security_headers(ok)


@pytest.mark.parametrize("app_env", ["development", "test"])
async def test_hsts_is_never_sent_outside_production(app_env: str) -> None:
    async with client_for(create_app(make_settings(app_env=app_env))) as client:
        response = await client.get("/api/v1/health")

    assert "strict-transport-security" not in response.headers


async def test_the_development_api_docs_may_load_their_assets() -> None:
    """Swagger UI pulls its script from a CDN and runs an inline bootstrap, which the JSON-only
    policy would block. The docs exist only outside production, so only they are exempt."""
    async with client_for(create_app(make_settings(app_env="development"))) as client:
        docs = await client.get("/api/docs")
        api = await client.get("/api/v1/health")

    assert docs.status_code == 200
    assert "content-security-policy" not in docs.headers
    assert docs.headers["x-content-type-options"] == "nosniff"
    assert docs.headers["x-frame-options"] == "DENY"
    assert api.headers["content-security-policy"] == EXPECTED["content-security-policy"]


async def test_production_has_no_docs_and_no_csp_exemption() -> None:
    async with client_for(create_app(production_settings())) as client:
        response = await client.get("/api/docs")

    assert response.status_code == 404
    assert_security_headers(response)
