"""A sweep across every HTTP route the app exposes: anonymous requests must be rejected unless
the route is one of the handful that have to work before anyone can sign in."""

import pytest

from app.main import create_http_app
from tests.conftest import make_settings
from tests.helpers import new_client

# Routes that must work before a student has signed in at all.
PUBLIC_ROUTES = frozenset(
    {
        ("GET", "/api/v1/health"),
        ("GET", "/api/v1/health/ready"),
        ("POST", "/api/v1/auth/register"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/verify"),
        ("POST", "/api/v1/auth/resend-verification"),
        ("POST", "/api/v1/auth/password-reset/request"),
        ("POST", "/api/v1/auth/password-reset/confirm"),
        # Deliberately a no-op (204) for a caller with no session, so signing out never gets
        # stuck: see app/modules/auth/router.py:logout.
        ("POST", "/api/v1/auth/logout"),
    }
)

# A syntactically valid id that can never match a real row.
PLACEHOLDER_ID = "00000000-0000-0000-0000-000000000000"


def _concrete_path(path: str) -> str:
    """Fills every {param} segment in an OpenAPI path template with PLACEHOLDER_ID."""
    while "{" in path:
        start = path.index("{")
        end = path.index("}", start)
        path = path[:start] + PLACEHOLDER_ID + path[end + 1 :]
    return path


def _protected_routes() -> list[tuple[str, str]]:
    schema = create_http_app(make_settings()).openapi()
    routes = []
    for template, methods in schema["paths"].items():
        for method in methods:
            if (method.upper(), template) in PUBLIC_ROUTES:
                continue
            routes.append((method.upper(), _concrete_path(template)))
    return sorted(set(routes))


ROUTES = _protected_routes()

# A canary: if the app ever stops exposing any routes (a broken router wire-up) this sweep
# would silently pass with zero cases. Guard against that going unnoticed.
assert len(ROUTES) >= 30, f"expected many protected routes, found {len(ROUTES)}: {ROUTES}"


@pytest.mark.usefixtures("clean_database")
@pytest.mark.parametrize(("method", "path"), ROUTES, ids=[f"{m}_{p}" for m, p in ROUTES])
async def test_every_protected_route_rejects_an_anonymous_request(
    app: object, method: str, path: str
) -> None:
    # new_client always sends the web Origin, so unsafe methods clear the CSRF-origin check
    # first and land on the real guard this test is pinning down: the session dependency.
    async with new_client(app) as anonymous:
        response = await anonymous.request(method, path)

    assert response.status_code == 401, (method, path, response.text)
    assert response.json()["error"]["code"] == "unauthorized"
