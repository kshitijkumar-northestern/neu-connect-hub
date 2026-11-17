"""Administrator MFA over the API: enrolment, confirmation, step-up verification, replay
protection, throttling and recovery codes."""

import asyncio
import base64
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.modules.admin import mfa_service
from app.modules.admin.recovery import hash_code
from app.modules.admin.totp import code_at, step_for
from tests.conftest import MemoryMailer
from tests.helpers import csrf, execute, login, new_client, scalar, student, user_id

pytestmark = pytest.mark.usefixtures("clean_database")

MFA_URL = "/api/v1/admin/mfa"
ADMIN_EMAIL = "ada@northeastern.edu"
NOW = datetime(2024, 1, 1, tzinfo=UTC)


async def make_admin(
    app: object, mailer: MemoryMailer, engine: AsyncEngine, email: str = ADMIN_EMAIL
) -> tuple[httpx.AsyncClient, str]:
    """A verified student promoted to admin the way the CLI does: a plain SQL update, the role
    column being the only thing that changes."""
    client = await student(app, mailer, email)
    admin_id = await user_id(client)
    await execute(engine, "UPDATE users SET role = 'admin' WHERE id = :id", id=admin_id)
    return client, admin_id


async def enroll(client: httpx.AsyncClient) -> httpx.Response:
    return await client.post(f"{MFA_URL}/enroll")


async def confirm(client: httpx.AsyncClient, code: str) -> httpx.Response:
    return await client.post(f"{MFA_URL}/confirm", json={"code": code})


async def verify(
    client: httpx.AsyncClient, *, code: str | None = None, recovery_code: str | None = None
) -> httpx.Response:
    body: dict[str, str] = {}
    if code is not None:
        body["code"] = code
    if recovery_code is not None:
        body["recovery_code"] = recovery_code
    return await client.post(f"{MFA_URL}/verify", json=body)


async def mfa_status(client: httpx.AsyncClient) -> httpx.Response:
    return await client.get(MFA_URL)


def code_from_secret(
    secret_b32: str, *, step: int | None = None, now: datetime | None = None
) -> str:
    """The current code for a secret returned by /enroll. Without `step`/`now`, this matches
    whatever the (real, unfrozen) server clock sees; tests that freeze the clock pass `step`
    explicitly, computed the same way."""
    padded = secret_b32 + "=" * (-len(secret_b32) % 8)
    raw = base64.b32decode(padded)
    if step is None:
        step = step_for(now if now is not None else datetime.now(UTC))
    return code_at(raw, step)


def freeze(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    """Pins the service's clock so replay and window behaviour is deterministic, never timing-
    dependent."""
    frozen = type("FrozenDatetime", (datetime,), {"now": classmethod(lambda cls, tz=None: moment)})
    monkeypatch.setattr(mfa_service, "datetime", frozen)


async def fetch_all(engine: AsyncEngine, sql: str, **params: object) -> list[Any]:
    async with engine.connect() as connection:
        result = await connection.execute(text(sql), params)
        return [row[0] for row in result.all()]


# --- access: students 404, anonymous 401 ----------------------------------------------------


async def test_anonymous_requests_get_401(client: httpx.AsyncClient) -> None:
    assert (await mfa_status(client)).status_code == 401
    assert (await enroll(client)).status_code == 401
    assert (await confirm(client, "000000")).status_code == 401
    assert (await verify(client, code="000000")).status_code == 401


async def test_students_get_404_on_every_mfa_route(app: object, mailer: MemoryMailer) -> None:
    other = await student(app, mailer, "grace@northeastern.edu")

    assert (await mfa_status(other)).status_code == 404
    assert (await enroll(other)).status_code == 404
    assert (await confirm(other, "000000")).status_code == 404
    assert (await verify(other, code="000000")).status_code == 404


# --- enrolment -------------------------------------------------------------------------------


async def test_enroll_then_confirm_returns_ten_recovery_codes_and_marks_the_session_verified(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin, _ = await make_admin(app, mailer, clean_database)

    enrolled = await enroll(admin)
    assert enrolled.status_code == 200
    body = enrolled.json()
    assert set(body) == {"secret", "otpauth_url"}
    assert body["secret"] in body["otpauth_url"]

    confirmed = await confirm(admin, code_from_secret(body["secret"]))
    assert confirmed.status_code == 200
    codes = confirmed.json()["recovery_codes"]
    assert len(codes) == 10
    assert len(set(codes)) == 10

    current = await mfa_status(admin)
    assert current.json() == {"enrolled": True, "verified": True, "recovery_codes_left": 10}


async def test_the_database_stores_only_a_nonce_never_a_secret(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin, admin_id = await make_admin(app, mailer, clean_database)
    await enroll(admin)

    columns = await scalar(
        clean_database,
        "SELECT array_agg(column_name ORDER BY column_name) FROM information_schema.columns "
        "WHERE table_name = 'admin_mfa'",
    )
    assert columns == ["confirmed_at", "created_at", "last_used_step", "nonce", "user_id"]
    nonce_length = await scalar(
        clean_database, "SELECT octet_length(nonce) FROM admin_mfa WHERE user_id = :id", id=admin_id
    )
    assert nonce_length == 16


async def test_recovery_codes_are_stored_only_as_sha256_hashes(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin, admin_id = await make_admin(app, mailer, clean_database)
    secret = (await enroll(admin)).json()["secret"]

    codes = (await confirm(admin, code_from_secret(secret))).json()["recovery_codes"]

    hashes = await fetch_all(
        clean_database,
        "SELECT code_hash FROM admin_recovery_codes WHERE user_id = :id",
        id=admin_id,
    )
    assert len(hashes) == 10
    assert not any(plain in hashes for plain in codes)
    assert sorted(hashes) == sorted(hash_code(plain) for plain in codes)


async def test_a_confirmed_enrolment_refuses_a_second_enroll(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin, _ = await make_admin(app, mailer, clean_database)
    secret = (await enroll(admin)).json()["secret"]
    await confirm(admin, code_from_secret(secret))

    again = await enroll(admin)

    assert again.status_code == 409
    assert again.json()["error"]["code"] == "mfa_already_enrolled"


async def test_confirm_without_a_pending_enrolment_is_409(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin, _ = await make_admin(app, mailer, clean_database)

    response = await confirm(admin, "000000")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mfa_not_pending"


async def test_confirm_with_a_wrong_code_is_422_and_the_enrolment_stays_pending(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin, _ = await make_admin(app, mailer, clean_database)
    secret = (await enroll(admin)).json()["secret"]
    real_code = code_from_secret(secret)
    wrong_code = "000000" if real_code != "000000" else "111111"

    bad = await confirm(admin, wrong_code)
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_code"

    good = await confirm(admin, real_code)
    assert good.status_code == 200


# --- verification: enrolment required, replay protection, throttling ------------------------


async def test_verify_without_enrolment_is_409(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin, _ = await make_admin(app, mailer, clean_database)

    response = await verify(admin, code="000000")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mfa_not_enrolled"


async def test_a_code_cannot_be_replayed_within_its_step(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin, _ = await make_admin(app, mailer, clean_database)
    freeze(monkeypatch, NOW)
    secret = (await enroll(admin)).json()["secret"]
    confirm_step = step_for(NOW) - 1
    assert (await confirm(admin, code_from_secret(secret, step=confirm_step))).status_code == 200

    verify_code = code_from_secret(secret, step=step_for(NOW))
    first = await verify(admin, code=verify_code)
    assert first.status_code == 200

    second = await verify(admin, code=verify_code)
    assert second.status_code == 422
    assert second.json()["error"]["code"] == "invalid_code"


async def test_a_code_for_the_next_step_works_then_the_current_steps_code_is_too_old(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin, _ = await make_admin(app, mailer, clean_database)
    freeze(monkeypatch, NOW)
    secret = (await enroll(admin)).json()["secret"]
    current = step_for(NOW)
    assert (await confirm(admin, code_from_secret(secret, step=current))).status_code == 200

    future = await verify(admin, code=code_from_secret(secret, step=current + 1))
    assert future.status_code == 200

    stale = await verify(admin, code=code_from_secret(secret, step=current))
    assert stale.status_code == 422
    assert stale.json()["error"]["code"] == "invalid_code"


async def test_six_correct_verifications_in_a_row_all_succeed(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only failures count against the shared throttle, so several admins (or tabs) verifying
    correctly in the same 5-minute window never lock each other, or themselves, out."""
    admin, _ = await make_admin(app, mailer, clean_database)
    freeze(monkeypatch, NOW)
    secret = (await enroll(admin)).json()["secret"]
    start = step_for(NOW)
    assert (await confirm(admin, code_from_secret(secret, step=start))).status_code == 200

    for offset in range(1, 7):  # six more correct, strictly increasing steps
        moment = NOW + timedelta(seconds=30 * offset)
        freeze(monkeypatch, moment)
        code = code_from_secret(secret, step=start + offset)
        response = await verify(admin, code=code)
        assert response.status_code == 200, response.text


async def test_five_wrong_attempts_then_a_correct_code_is_still_throttled(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin, _ = await make_admin(app, mailer, clean_database)
    secret = (await enroll(admin)).json()["secret"]
    real_code = code_from_secret(secret)
    wrong_code = "000000" if real_code != "000000" else "111111"
    assert (await confirm(admin, real_code)).status_code == 200  # a success: doesn't count

    for _ in range(5):
        assert (await verify(admin, code=wrong_code)).status_code == 422

    sixth = await verify(admin, code=real_code)  # correct, but the budget is already spent
    assert sixth.status_code == 429
    assert "Retry-After" in sixth.headers


@pytest.mark.parametrize(
    "malformed",
    [
        "12345é",  # non-ASCII: must never 500 from hmac.compare_digest
        "abcdef",
        "12345",
        "1234567",
    ],
)
async def test_malformed_codes_are_422_invalid_code_never_500(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine, malformed: str
) -> None:
    admin, _ = await make_admin(app, mailer, clean_database)
    secret = (await enroll(admin)).json()["secret"]
    await confirm(admin, code_from_secret(secret))

    response = await verify(admin, code=malformed)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_code"


# --- recovery codes ---------------------------------------------------------------------------


async def test_each_recovery_code_works_once_even_with_concurrent_use(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin, _ = await make_admin(app, mailer, clean_database)
    secret = (await enroll(admin)).json()["secret"]
    codes = (await confirm(admin, code_from_secret(secret))).json()["recovery_codes"]
    target = codes[0]

    first, second = await asyncio.gather(
        verify(admin, recovery_code=target), verify(admin, recovery_code=target)
    )

    assert sorted([first.status_code, second.status_code]) == [200, 422]
    left = await mfa_status(admin)
    assert left.json()["recovery_codes_left"] == 9


async def test_a_used_recovery_code_is_rejected_again(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin, _ = await make_admin(app, mailer, clean_database)
    secret = (await enroll(admin)).json()["secret"]
    codes = (await confirm(admin, code_from_secret(secret))).json()["recovery_codes"]
    target = codes[1]
    assert (await verify(admin, recovery_code=target)).status_code == 200

    repeated = await verify(admin, recovery_code=target)

    assert repeated.status_code == 422
    assert repeated.json()["error"]["code"] == "invalid_code"


# --- expiry ------------------------------------------------------------------------------------


async def test_verification_expires_after_the_max_age(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine, settings: Settings
) -> None:
    admin, admin_id = await make_admin(app, mailer, clean_database)
    secret = (await enroll(admin)).json()["secret"]
    await confirm(admin, code_from_secret(secret))
    assert (await mfa_status(admin)).json()["verified"] is True

    await execute(
        clean_database,
        "UPDATE sessions SET mfa_verified_at = now() - make_interval(secs => :age) "
        "WHERE user_id = :id",
        age=float(settings.admin_mfa_max_age_seconds + 60),
        id=admin_id,
    )

    expired = await mfa_status(admin)
    assert expired.json()["verified"] is False


async def test_a_new_session_is_not_verified_even_though_already_enrolled(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin, _ = await make_admin(app, mailer, clean_database)
    secret = (await enroll(admin)).json()["secret"]
    await confirm(admin, code_from_secret(secret))

    second_session = new_client(app)
    try:
        login_response = await login(second_session, ADMIN_EMAIL)
        assert login_response.status_code == 200
        second_session.headers.update(csrf(second_session))

        result = await mfa_status(second_session)
        assert result.json() == {
            "enrolled": True,
            "verified": False,
            "recovery_codes_left": 10,
        }
    finally:
        await second_session.aclose()
