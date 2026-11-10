import asyncio
import hashlib
import logging

import httpx
import pytest
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.rate_limit import Limit
from app.core.security import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    current_session,
    require_verified,
)
from app.main import create_app, create_http_app
from app.modules.auth.passwords import PasswordService
from app.modules.auth.sessions import CurrentSession
from tests.conftest import WEB_ORIGIN, MemoryMailer, make_settings
from tests.helpers import (
    EMAIL,
    PASSWORD,
    client_from,
    csrf,
    execute,
    login,
    register,
    scalar,
    signed_in,
    verify_last,
)

pytestmark = pytest.mark.usefixtures("clean_database")

NEW_PASSWORD = "a different long passphrase"


# --- registration -------------------------------------------------------------------------


async def test_registration_creates_an_unverified_account_and_emails_a_link(
    client: httpx.AsyncClient, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    response = await register(client)

    assert response.status_code == 202
    assert response.json() == {"status": "verification_sent"}
    assert len(mailer.sent) == 1 and mailer.sent[0].to == EMAIL
    assert f"{WEB_ORIGIN}/verify-email#token=" in mailer.sent[0].text
    stored = await scalar(
        clean_database, "SELECT password_hash FROM users WHERE email = :e", e=EMAIL
    )
    assert isinstance(stored, str) and stored.startswith("$argon2id$")
    assert (
        await scalar(clean_database, "SELECT verified_at FROM users WHERE email = :e", e=EMAIL)
        is None
    )


async def test_non_campus_domains_are_rejected(
    client: httpx.AsyncClient, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    response = await register(client, "ada@gmail.com")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "email_domain_not_allowed"
    assert mailer.sent == []
    assert await scalar(clean_database, "SELECT count(*) FROM users") == 0


@pytest.mark.parametrize(
    "variant", ["Ada@Northeastern.edu", "  ada@northeastern.edu  ", "ADA@NORTHEASTERN.EDU"]
)
async def test_duplicate_registration_looks_identical_and_creates_nothing(
    client: httpx.AsyncClient, mailer: MemoryMailer, clean_database: AsyncEngine, variant: str
) -> None:
    first = await register(client)
    second = await register(client, variant, "another long password")

    assert (second.status_code, second.json()) == (first.status_code, first.json())
    assert await scalar(clean_database, "SELECT count(*) FROM users") == 1
    assert len(mailer.sent) == 1


async def test_registering_an_existing_email_still_costs_a_password_hash(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Equal work on both paths, so response time doesn't reveal that the account exists."""
    await register(client)
    calls: list[str] = []
    real_hash = PasswordService.hash

    async def spy(self: PasswordService, password: str) -> str:
        calls.append(password)
        return await real_hash(self, password)

    monkeypatch.setattr(PasswordService, "hash", spy)
    await register(client, password="another long password")

    assert calls == ["another long password"]


async def test_short_passwords_are_rejected_with_a_field_error(
    client: httpx.AsyncClient, clean_database: AsyncEngine
) -> None:
    response = await register(client, password="short")

    assert response.status_code == 422
    assert [f["location"] for f in response.json()["error"]["fields"]] == ["body.password"]
    assert "short" not in response.text
    assert await scalar(clean_database, "SELECT count(*) FROM users") == 0


# --- email verification --------------------------------------------------------------------


async def test_a_verification_link_works_once(
    client: httpx.AsyncClient, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    await register(client)

    first = await verify_last(client, mailer)
    reused = await verify_last(client, mailer)

    assert first.status_code == 200
    assert await scalar(clean_database, "SELECT verified_at FROM users") is not None
    assert reused.status_code == 400
    assert reused.json()["error"]["code"] == "invalid_token"


async def test_an_expired_verification_link_fails(
    client: httpx.AsyncClient, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    await register(client)
    await execute(
        clean_database, "UPDATE email_verifications SET expires_at = now() - interval '1 second'"
    )

    response = await verify_last(client, mailer)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_token"
    assert await scalar(clean_database, "SELECT verified_at FROM users") is None


@pytest.mark.parametrize("token", ["x" * 43, "not a token at all!!!!!!!!!!", "a" * 200])
async def test_unknown_or_malformed_verification_tokens_fail_safely(
    client: httpx.AsyncClient, token: str
) -> None:
    response = await client.post("/api/v1/auth/verify", json={"token": token})

    assert response.status_code in (400, 422)
    assert response.json()["error"]["code"] in ("invalid_token", "validation_error")


async def test_resending_replaces_the_previous_link(
    client: httpx.AsyncClient, mailer: MemoryMailer
) -> None:
    await register(client)
    old = mailer.token_from_last("/verify-email")

    resend = await client.post("/api/v1/auth/resend-verification", json={"email": EMAIL})

    assert resend.status_code == 202
    assert (await client.post("/api/v1/auth/verify", json={"token": old})).status_code == 400
    assert (await verify_last(client, mailer)).status_code == 200


async def test_resend_for_an_unknown_address_looks_the_same_and_sends_nothing(
    client: httpx.AsyncClient, mailer: MemoryMailer
) -> None:
    response = await client.post(
        "/api/v1/auth/resend-verification", json={"email": "nobody@northeastern.edu"}
    )

    assert (response.status_code, response.json()) == (202, {"status": "verification_sent"})
    assert mailer.sent == []


# --- login and sessions --------------------------------------------------------------------


async def test_two_users_can_register_verify_and_sign_in(
    client: httpx.AsyncClient, mailer: MemoryMailer
) -> None:
    """Two separate accounts can each register, verify their email and sign in."""
    for email in (EMAIL, "grace@northeastern.edu"):
        client.cookies.clear()
        await signed_in(client, mailer, email)
        me = await client.get("/api/v1/me")
        assert me.status_code == 200
        assert me.json()["email"] == email
        assert me.json()["verified"] is True


async def test_login_sets_secure_cookie_flags_and_stores_only_a_session_hash(
    client: httpx.AsyncClient, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    response = await signed_in(client, mailer)

    cookies = {c.split("=", 1)[0]: c.lower() for c in response.headers.get_list("set-cookie")}
    assert "httponly" in cookies[SESSION_COOKIE] and "samesite=lax" in cookies[SESSION_COOKIE]
    assert "httponly" not in cookies[CSRF_COOKIE] and "samesite=lax" in cookies[CSRF_COOKIE]
    raw = client.cookies[SESSION_COOKIE]
    stored = await scalar(clean_database, "SELECT id_hash FROM sessions")
    assert stored == hashlib.sha256(raw.encode()).hexdigest() and stored != raw
    assert "password" not in response.text and "$argon2" not in response.text


async def test_cookies_are_secure_when_the_site_is_https(mailer: MemoryMailer) -> None:
    app = create_http_app(make_settings(web_origin="https://hub.example"), mailer=mailer)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
        headers={"Origin": "https://hub.example"},
    ) as client:
        await signed_in(client, mailer)
        response = await login(client)

    assert all("secure" in c.lower() for c in response.headers.get_list("set-cookie"))


async def test_unverified_users_can_sign_in_but_not_reach_verified_routes(
    mailer: MemoryMailer,
) -> None:
    app = create_http_app(make_settings(), mailer=mailer)

    @app.get("/api/v1/_verified-probe", dependencies=[Depends(require_verified)])
    async def probe() -> dict[str, bool]:
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": WEB_ORIGIN},
    ) as client:
        await register(client)
        assert (await login(client)).status_code == 200
        assert (await client.get("/api/v1/me")).json()["verified"] is False
        blocked = await client.get("/api/v1/_verified-probe")
        await verify_last(client, mailer)
        allowed = await client.get("/api/v1/_verified-probe")

    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "verification_required"
    assert allowed.status_code == 200


async def test_wrong_password_and_unknown_email_get_the_same_answer(
    client: httpx.AsyncClient, mailer: MemoryMailer
) -> None:
    await register(client)

    wrong = await login(client, password="not the right password")
    unknown = await login(client, email="nobody@northeastern.edu")

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"] == "invalid_credentials"
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


async def test_repeated_failures_throttle_the_account(
    client: httpx.AsyncClient, mailer: MemoryMailer
) -> None:
    await register(client)
    for _ in range(5):
        assert (await login(client, password="not the right password")).status_code == 401

    blocked = await login(client)  # even the right password waits

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) > 0


async def test_concurrent_failures_cannot_exceed_the_lockout(
    client: httpx.AsyncClient, mailer: MemoryMailer
) -> None:
    """Attempts are counted before the password check, so racing requests can't slip past."""
    await register(client)

    responses = await asyncio.gather(
        *(login(client, password="not the right password") for _ in range(10))
    )

    codes = [r.status_code for r in responses]
    assert codes.count(401) == 5
    assert codes.count(429) == 5


async def test_failures_from_one_address_do_not_lock_out_another(mailer: MemoryMailer) -> None:
    app = create_app(make_settings(), mailer=mailer)
    async with client_from(app, "10.0.0.1") as attacker, client_from(app, "10.0.0.2") as owner:
        await register(owner)
        for _ in range(5):
            await login(attacker, password="not the right password")

        assert (await login(attacker)).status_code == 429
        assert (await login(owner)).status_code == 200


async def test_an_account_wide_cap_bounds_guessing_from_many_addresses(
    mailer: MemoryMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.modules.auth.service.LOGIN_ATTEMPTS_PER_ACCOUNT",
        Limit(max_events=3, window_seconds=3600),
    )
    app = create_app(make_settings(), mailer=mailer)
    codes = []
    for ip in ("10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"):
        async with client_from(app, ip) as client:
            if ip == "10.0.0.1":
                await register(client)
            codes.append((await login(client, password="not the right password")).status_code)

    assert codes == [401, 401, 401, 429]


async def test_suspended_accounts_cannot_sign_in_or_keep_a_session(
    client: httpx.AsyncClient, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    await signed_in(client, mailer)
    await execute(clean_database, "UPDATE users SET suspended_at = now()")

    me = await client.get("/api/v1/me")
    wrong = await login(client, password="not the right password")
    right = await login(client)

    assert me.status_code == 401
    assert wrong.status_code == 401 and wrong.json()["error"]["code"] == "invalid_credentials"
    assert right.status_code == 403 and right.json()["error"]["code"] == "account_suspended"


async def test_me_requires_a_session(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_logout_ends_the_session_server_side(
    client: httpx.AsyncClient, mailer: MemoryMailer
) -> None:
    await signed_in(client, mailer)
    old_cookie = client.cookies[SESSION_COOKIE]

    response = await client.post("/api/v1/auth/logout")  # no CSRF token needed

    assert response.status_code == 204
    cleared = [c.lower() for c in response.headers.get_list("set-cookie")]
    assert any(c.startswith(f"{SESSION_COOKIE}=") and "max-age=0" in c for c in cleared)
    assert any(c.startswith(f"{CSRF_COOKIE}=") and "max-age=0" in c for c in cleared)
    client.cookies.set(SESSION_COOKIE, old_cookie)
    assert (await client.get("/api/v1/me")).status_code == 401


async def test_logout_still_requires_the_web_origin(
    client: httpx.AsyncClient, mailer: MemoryMailer
) -> None:
    await signed_in(client, mailer)

    response = await client.post("/api/v1/auth/logout", headers={"Origin": "https://evil.example"})

    assert response.status_code == 403
    assert (await client.get("/api/v1/me")).status_code == 200


async def test_signing_in_again_replaces_the_previous_session(
    client: httpx.AsyncClient, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    await signed_in(client, mailer)
    first = client.cookies[SESSION_COOKIE]

    await login(client)

    assert client.cookies[SESSION_COOKIE] != first
    assert await scalar(clean_database, "SELECT count(*) FROM sessions") == 1


@pytest.mark.parametrize(
    "expire_sql",
    [
        "UPDATE sessions SET expires_at = now() - interval '1 second'",
        "UPDATE sessions SET last_seen_at = now() - interval '8 days'",
    ],
    ids=["absolute-lifetime", "idle-timeout"],
)
async def test_expired_sessions_are_rejected(
    client: httpx.AsyncClient, mailer: MemoryMailer, clean_database: AsyncEngine, expire_sql: str
) -> None:
    """Expired sessions fail safely."""
    await signed_in(client, mailer)
    await execute(clean_database, expire_sql)

    response = await client.get("/api/v1/me")

    assert response.status_code == 401
    assert await scalar(clean_database, "SELECT count(*) FROM sessions") == 0


# --- origin and CSRF -----------------------------------------------------------------------


async def test_unsafe_requests_from_another_origin_are_refused(client: httpx.AsyncClient) -> None:
    for headers in ({"Origin": "https://evil.example"}, {"Origin": ""}):
        response = await client.post(
            "/api/v1/auth/register", json={"email": EMAIL, "password": PASSWORD}, headers=headers
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "origin_not_allowed"


async def test_signed_in_unsafe_requests_need_the_matching_csrf_token(
    mailer: MemoryMailer,
) -> None:
    app = create_http_app(make_settings(), mailer=mailer)

    @app.post("/api/v1/_csrf-probe")
    async def probe(current: CurrentSession = Depends(current_session)) -> dict[str, bool]:
        return {"ok": True}

    async with client_from(app, "127.0.0.1") as client:
        await signed_in(client, mailer)
        missing = await client.post("/api/v1/_csrf-probe")
        wrong = await client.post(
            "/api/v1/_csrf-probe", headers={CSRF_HEADER: "forged-token-value"}
        )
        right = await client.post("/api/v1/_csrf-probe", headers=csrf(client))

    assert missing.status_code == wrong.status_code == 403
    assert missing.json()["error"]["code"] == wrong.json()["error"]["code"] == "csrf_failed"
    assert right.status_code == 200


# --- password reset ------------------------------------------------------------------------


async def test_password_reset_changes_the_password_and_ends_every_session(
    client: httpx.AsyncClient, mailer: MemoryMailer
) -> None:
    await signed_in(client, mailer)
    old_session = client.cookies[SESSION_COOKIE]

    requested = await client.post("/api/v1/auth/password-reset/request", json={"email": EMAIL})
    token = mailer.token_from_last("/reset-password")
    confirmed = await client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": token, "password": NEW_PASSWORD}
    )

    assert requested.status_code == 202 and confirmed.status_code == 200
    client.cookies.set(SESSION_COOKIE, old_session)
    assert (await client.get("/api/v1/me")).status_code == 401
    assert (await login(client)).status_code == 401
    assert (await login(client, password=NEW_PASSWORD)).status_code == 200


async def test_a_reset_link_works_once(client: httpx.AsyncClient, mailer: MemoryMailer) -> None:
    await register(client)
    await client.post("/api/v1/auth/password-reset/request", json={"email": EMAIL})
    token = mailer.token_from_last("/reset-password")
    body = {"token": token, "password": NEW_PASSWORD}

    first = await client.post("/api/v1/auth/password-reset/confirm", json=body)
    reused = await client.post("/api/v1/auth/password-reset/confirm", json=body)

    assert first.status_code == 200
    assert reused.status_code == 400 and reused.json()["error"]["code"] == "invalid_token"


async def test_an_expired_reset_link_fails(
    client: httpx.AsyncClient, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    await register(client)
    await client.post("/api/v1/auth/password-reset/request", json={"email": EMAIL})
    await execute(
        clean_database, "UPDATE password_resets SET expires_at = now() - interval '1 second'"
    )

    response = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": mailer.token_from_last("/reset-password"), "password": NEW_PASSWORD},
    )

    assert response.status_code == 400
    assert (await login(client)).status_code == 200  # old password still works


async def test_reset_requests_for_unknown_addresses_look_the_same(
    client: httpx.AsyncClient, mailer: MemoryMailer
) -> None:
    response = await client.post(
        "/api/v1/auth/password-reset/request", json={"email": "nobody@northeastern.edu"}
    )

    assert (response.status_code, response.json()) == (202, {"status": "reset_requested"})
    assert mailer.sent == []


# --- nothing sensitive leaks ---------------------------------------------------------------


async def test_tokens_and_passwords_never_reach_the_logs(
    client: httpx.AsyncClient, mailer: MemoryMailer, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        await signed_in(client, mailer)
        await client.post("/api/v1/auth/password-reset/request", json={"email": EMAIL})
        reset_token = mailer.token_from_last("/reset-password")
        await client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": reset_token, "password": NEW_PASSWORD},
        )

    logged = caplog.text + " ".join(str(vars(r)) for r in caplog.records)
    for secret in (PASSWORD, NEW_PASSWORD, reset_token, mailer.token_from_last("/verify-email")):
        assert secret not in logged
