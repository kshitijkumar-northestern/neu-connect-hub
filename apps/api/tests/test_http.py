import logging
import re
import time

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app, create_http_app
from tests.conftest import make_settings, production_settings

HEX_ID = re.compile(r"^[0-9a-f]{32}$")


def client_for(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_returns_ok_with_a_request_id(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert HEX_ID.match(response.headers["x-request-id"])


async def test_a_valid_incoming_request_id_is_kept(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health", headers={"X-Request-ID": "edge-proxy-1234abcd"})

    assert response.headers["x-request-id"] == "edge-proxy-1234abcd"


@pytest.mark.parametrize("bad_id", ["short", "has spaces in it", "x" * 200, "semi;colon-12345"])
async def test_an_invalid_incoming_request_id_is_replaced(
    client: httpx.AsyncClient, bad_id: str
) -> None:
    response = await client.get("/api/v1/health", headers={"X-Request-ID": bad_id})

    assert HEX_ID.match(response.headers["x-request-id"])


async def test_unknown_routes_use_the_error_envelope(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/nope")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["request_id"] == response.headers["x-request-id"]


async def test_wrong_methods_use_the_error_envelope(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


async def test_validation_errors_list_fields_without_echoing_input(settings: Settings) -> None:
    app = create_http_app(settings)

    @app.get("/api/v1/_probe/{item_id}")
    async def probe(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    async with client_for(app) as client:
        response = await client.get("/api/v1/_probe/not-a-number-hunter2")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert [field["location"] for field in error["fields"]] == ["path.item_id"]
    assert "hunter2" not in response.text


async def test_unhandled_errors_are_generic_and_logged_with_the_request_id(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_http_app(settings)

    @app.get("/api/v1/_boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    with caplog.at_level(logging.ERROR):
        async with client_for(app) as client:
            response = await client.get("/api/v1/_boom")

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert "secret internal detail" not in response.text
    assert error["request_id"] == response.headers["x-request-id"]
    logged = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert logged and logged[-1].exc_info is not None
    assert getattr(logged[-1], "request_id", None) == error["request_id"]


@pytest.mark.usefixtures("migrated")
async def test_readiness_checks_the_database(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_readiness_returns_503_quickly_when_the_database_is_down() -> None:
    down = make_settings(database_url="postgresql+asyncpg://app:local@127.0.0.1:1/nu_hub_test")

    started = time.monotonic()
    async with client_for(create_http_app(down)) as client:
        response = await client.get("/api/v1/health/ready")
    elapsed = time.monotonic() - started

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_ready"
    assert elapsed < down.readiness_timeout_seconds + 1


async def test_api_docs_are_served_outside_production_only() -> None:
    dev = create_app(make_settings(app_env="development"))
    prod = create_app(production_settings())

    async with client_for(dev) as client:  # type: ignore[arg-type]
        assert (await client.get("/api/docs")).status_code == 200
    async with client_for(prod) as client:  # type: ignore[arg-type]
        assert (await client.get("/api/docs")).status_code == 404
        assert (await client.get("/api/openapi.json")).status_code == 404
