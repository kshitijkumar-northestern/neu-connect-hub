from collections.abc import AsyncIterator

import httpx

from app.main import create_app
from tests.conftest import make_settings
from tests.helpers import new_client

LIMIT = 4096


def app_with_limit() -> object:
    return create_app(make_settings(max_request_body_bytes=LIMIT))


async def test_bodies_declared_over_the_limit_are_refused_before_parsing() -> None:
    async with new_client(app_with_limit()) as client:
        response = await client.post(
            "/api/v1/auth/login",
            content=b'{"email": "' + b"a" * LIMIT + b'"}',
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert response.headers["X-Request-ID"]


async def test_streamed_bodies_are_cut_off_at_the_limit() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b'{"email": "'
        for _ in range(8):
            yield b"a" * 1024
        yield b'"}'

    async with new_client(app_with_limit()) as client:
        response = await client.post(
            "/api/v1/auth/login", content=chunks(), headers={"Content-Type": "application/json"}
        )
    assert "content-length" not in {k.lower() for k in response.request.headers}
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


async def test_bodies_within_the_limit_reach_the_route() -> None:
    async with new_client(app_with_limit()) as client:
        response = await client.post("/api/v1/auth/login", json={"email": "not-an-email"})
    assert response.status_code == 422


def test_the_default_limit_fits_a_full_profile() -> None:
    assert make_settings().max_request_body_bytes >= 32 * 1024


async def test_requests_without_bodies_are_unaffected() -> None:
    async with new_client(app_with_limit()) as client:
        response: httpx.Response = await client.get("/api/v1/health")
    assert response.status_code == 200
