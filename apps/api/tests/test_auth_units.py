import hashlib
import logging
from pathlib import Path
from typing import Any

import pytest

from app.core.mail import DevMailer, EmailMessage, SmtpMailer
from app.core.rate_limit import Limit, RateLimiter
from app.modules.auth.passwords import PasswordService
from app.modules.auth.tokens import hash_token, new_token
from tests.conftest import make_settings

# --- rate limiter --------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_the_limit_allows_exactly_max_events_per_window() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    limit = Limit(max_events=3, window_seconds=60)

    results = [limiter.hit("login", "ip", limit) for _ in range(4)]

    assert results[:3] == [None, None, None]
    assert results[3] == pytest.approx(60)


def test_events_age_out_of_the_window() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    limit = Limit(max_events=1, window_seconds=60)
    limiter.hit("b", "k", limit)

    clock.now += 59
    assert limiter.check("b", "k", limit) == pytest.approx(1)
    clock.now += 2
    assert limiter.check("b", "k", limit) is None


def test_buckets_and_keys_are_independent_and_reset_clears() -> None:
    limiter = RateLimiter(clock=FakeClock())
    limit = Limit(max_events=1, window_seconds=60)
    limiter.hit("login", "a", limit)

    assert limiter.check("login", "b", limit) is None
    assert limiter.check("register", "a", limit) is None
    limiter.reset("login", "a")
    assert limiter.check("login", "a", limit) is None


def test_stale_keys_are_pruned_so_memory_stays_bounded() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock=clock, prune_threshold=10)
    limit = Limit(max_events=1, window_seconds=60)
    for i in range(10):
        limiter.hit("b", f"k{i}", limit)

    clock.now += 61
    limiter.hit("b", "fresh", limit)

    assert len(limiter) == 1


def test_keys_still_inside_their_window_survive_pruning() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock=clock, prune_threshold=3)
    short, long = Limit(max_events=5, window_seconds=10), Limit(max_events=5, window_seconds=600)
    limiter.hit("short", "a", short)
    limiter.hit("long", "b", long)
    limiter.hit("long", "c", long)

    clock.now += 11
    limiter.hit("long", "d", long)

    assert len(limiter) == 3  # "a" expired; b, c and d are still inside their 600 s window
    assert limiter.check("long", "b", Limit(max_events=1, window_seconds=600)) is not None


def test_check_does_not_record_an_event() -> None:
    limiter = RateLimiter(clock=FakeClock())
    limit = Limit(max_events=1, window_seconds=60)

    for _ in range(5):
        assert limiter.check("b", "k", limit) is None


# --- passwords and tokens ------------------------------------------------------------------


async def test_passwords_hash_with_argon2id_and_verify() -> None:
    service = PasswordService(make_settings())

    hashed = await service.hash("correct horse battery")

    assert hashed.startswith("$argon2id$")
    assert await service.verify(hashed, "correct horse battery") is True
    assert await service.verify(hashed, "wrong horse battery") is False


async def test_verifying_without_an_account_is_always_false() -> None:
    """The dummy hash keeps timing similar for unknown emails, but can never succeed."""
    service = PasswordService(make_settings())

    assert await service.verify(None, "timing-equaliser") is False
    assert await service.verify(None, "anything") is False


async def test_corrupt_hashes_fail_closed() -> None:
    assert await PasswordService(make_settings()).verify("not-a-hash", "x") is False


async def test_hashes_made_with_weaker_parameters_need_a_rehash() -> None:
    weak = PasswordService(make_settings(argon2_time_cost=1))
    strong = PasswordService(make_settings(argon2_time_cost=2))

    assert strong.needs_rehash(await weak.hash("correct horse battery")) is True
    assert strong.needs_rehash(await strong.hash("correct horse battery")) is False


def test_tokens_are_random_url_safe_and_stored_only_as_sha256() -> None:
    raw, digest = new_token()
    other, _ = new_token()

    assert raw != other and len(raw) >= 43
    assert set(raw) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    assert digest == hashlib.sha256(raw.encode()).hexdigest() == hash_token(raw)


# --- mailers -------------------------------------------------------------------------------

MESSAGE = EmailMessage(to="ada@northeastern.edu", subject="Verify", text="secret-link-token-123")


async def test_the_dev_mailer_writes_a_file_and_never_logs_the_body(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    mailer = DevMailer(tmp_path / "mail")

    with caplog.at_level(logging.DEBUG):
        await mailer.send(MESSAGE)

    files = list((tmp_path / "mail").iterdir())
    assert len(files) == 1
    content = files[0].read_text()
    assert "To: ada@northeastern.edu" in content and "secret-link-token-123" in content
    assert "secret-link-token-123" not in caplog.text
    assert "ada@northeastern.edu" not in caplog.text


async def test_the_smtp_mailer_uses_starttls_and_logs_in(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, Any]] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            calls.append(("connect", (host, port)))

        def __enter__(self) -> "FakeSMTP":
            return self

        def __exit__(self, *exc: object) -> None:
            calls.append(("quit", None))

        def starttls(self, context: object) -> None:
            calls.append(("starttls", context is not None))

        def login(self, user: str, password: str) -> None:
            calls.append(("login", (user, password)))

        def send_message(self, message: Any) -> None:
            calls.append(("send", (message["To"], message["Subject"], message["From"])))

    monkeypatch.setattr("app.core.mail.smtplib.SMTP", FakeSMTP)
    settings = make_settings(
        mail_backend="smtp",
        smtp_host="smtp.example.com",
        smtp_username="apikey",
        email_provider_api_key="k3y",
    )

    await SmtpMailer(settings).send(MESSAGE)

    assert calls == [
        ("connect", ("smtp.example.com", 587)),
        ("starttls", True),
        ("login", ("apikey", "k3y")),
        ("send", ("ada@northeastern.edu", "Verify", settings.mail_from)),
        ("quit", None),
    ]
