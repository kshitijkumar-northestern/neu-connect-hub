"""Security settings: a remote database must be reached over TLS in production, and the
Socket.IO frame cap stays within sensible bounds."""

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from tests.conftest import make_settings, production_settings

REMOTE = "postgresql+asyncpg://app_runtime:secret@db.internal.example:5432/nu_hub"
HOSTLESS = "postgresql+asyncpg://app@/nu_hub"


@pytest.fixture(autouse=True)
def no_pghost(monkeypatch: pytest.MonkeyPatch) -> None:
    """asyncpg falls back to PGHOST for a URL without a host; a developer's shell must not
    decide these tests."""
    monkeypatch.delenv("PGHOST", raising=False)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://app:local@localhost:5432/nu_hub",
        "postgresql+asyncpg://app:local@127.0.0.1:5432/nu_hub",
        "postgresql+asyncpg://app:local@127.0.0.2/nu_hub",
        "postgresql+asyncpg://app:local@[::1]:5432/nu_hub",
        "postgresql+asyncpg://app:local@LocalHost/nu_hub",
        # A Unix socket: no host at all, or a socket directory.
        "postgresql+asyncpg://app@/nu_hub",
        "postgresql+asyncpg://app@/nu_hub?host=/var/run/postgresql",
    ],
)
def test_production_accepts_a_local_database_without_tls(url: str) -> None:
    assert production_settings(database_url=url).database_url == url


@pytest.mark.parametrize("mode", ["require", "verify-ca", "verify-full"])
def test_production_accepts_a_remote_database_that_requires_tls(mode: str) -> None:
    url = f"{REMOTE}?ssl={mode}"

    assert production_settings(database_url=url).database_url == url


@pytest.mark.parametrize(
    "url",
    [
        REMOTE,
        f"{REMOTE}?ssl=disable",
        # These fall back to plaintext when the server doesn't offer TLS.
        f"{REMOTE}?ssl=allow",
        f"{REMOTE}?ssl=prefer",
        f"{REMOTE}?application_name=api",
        "postgresql+asyncpg://app:x@10.0.0.5:5432/nu_hub",
        "postgresql+asyncpg://app:x@localhost.evil.example/nu_hub",
        # A multi-host URL where any host is remote.
        "postgresql+asyncpg://app:x@/nu_hub?host=localhost:5432&host=db.internal.example:5432",
    ],
)
def test_production_refuses_a_remote_database_without_tls(url: str) -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        production_settings(database_url=url)


@pytest.mark.parametrize(
    "url",
    [
        # SQLAlchemy hands the query to asyncpg after the URL's own host, so ?host= wins.
        "postgresql+asyncpg://app:x@localhost/nu_hub?host=db.remote.example",
        "postgresql+asyncpg://app:x@localhost:5432/nu_hub?host=db.remote.example:5432",
        "postgresql+asyncpg://app:x@127.0.0.1/nu_hub?host=10.0.0.5",
        # A socket directory followed by a remote host in one comma list.
        "postgresql+asyncpg://app:x@/nu_hub?host=/tmp,db.remote.example",
        "postgresql+asyncpg://app:x@localhost/nu_hub?host=localhost,db.remote.example",
    ],
)
def test_production_refuses_a_remote_host_hidden_in_the_query(url: str) -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        production_settings(database_url=url)


@pytest.mark.parametrize(
    "url",
    [
        # The query may still point a local URL at a local socket or loopback address.
        "postgresql+asyncpg://app:x@localhost/nu_hub?host=/var/run/postgresql",
        "postgresql+asyncpg://app:x@db.remote.example/nu_hub?host=/tmp",
        "postgresql+asyncpg://app:x@/nu_hub?host=127.0.0.1:5432&host=localhost:5433",
    ],
)
def test_production_accepts_query_hosts_that_are_all_local(url: str) -> None:
    assert production_settings(database_url=url).database_url == url


@pytest.mark.parametrize(
    "pghost", ["db.remote.example", "10.0.0.5:5432", "/var/run/postgresql,db.remote.example"]
)
def test_production_refuses_a_hostless_url_when_pghost_is_remote(
    monkeypatch: pytest.MonkeyPatch, pghost: str
) -> None:
    """With no host in the URL, asyncpg connects to PGHOST, with sslmode prefer."""
    monkeypatch.setenv("PGHOST", pghost)

    with pytest.raises(ValidationError, match="DATABASE_URL"):
        production_settings(database_url=HOSTLESS)


@pytest.mark.parametrize("pghost", ["localhost", "127.0.0.1:5432", "/var/run/postgresql"])
def test_production_accepts_a_hostless_url_when_pghost_is_local(
    monkeypatch: pytest.MonkeyPatch, pghost: str
) -> None:
    monkeypatch.setenv("PGHOST", pghost)

    assert production_settings(database_url=HOSTLESS).database_url == HOSTLESS


def test_pghost_is_ignored_when_the_url_names_a_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGHOST", "db.remote.example")
    local = "postgresql+asyncpg://app:x@localhost/nu_hub"

    assert production_settings(database_url=local).database_url == local


def test_a_remote_pghost_with_tls_requested_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGHOST", "db.remote.example")
    url = f"{HOSTLESS}?ssl=require"

    assert production_settings(database_url=url).database_url == url


def test_a_remote_pghost_is_fine_outside_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGHOST", "db.remote.example")

    assert make_settings(database_url=HOSTLESS).database_url == HOSTLESS


@pytest.mark.parametrize("make", [make_settings, production_settings])
def test_an_unparseable_url_never_echoes_the_password(make: Callable[..., Settings]) -> None:
    """An unencoded @ or : in the password breaks parsing, and the parser's own message would
    quote the text after it."""
    with pytest.raises(ValidationError, match="DATABASE_URL") as caught:
        make(database_url="postgresql+asyncpg://app:s3cr@t:pass@db.example/nu_hub")

    message = str(caught.value)
    assert "pass" not in message.replace("DATABASE_URL", "")
    assert "s3cr" not in message
    assert "db.example" not in message


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://app:x@db.example:notaport/nu_hub",
        "postgresql+asyncpg://app:x@h1:5432,h2:5433/nu_hub",
    ],
)
def test_an_unusable_url_is_refused_with_a_generic_message(url: str) -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL is not a valid database URL") as caught:
        make_settings(database_url=url)

    assert "notaport" not in str(caught.value) and "h2" not in str(caught.value)


def test_production_refusal_names_the_parameter_to_add() -> None:
    with pytest.raises(ValidationError, match=r"ssl=require"):
        production_settings(database_url=REMOTE)


def test_a_remote_database_without_tls_is_fine_outside_production() -> None:
    assert make_settings(database_url=REMOTE).database_url == REMOTE


@pytest.mark.parametrize("make", [make_settings, production_settings])
def test_sslmode_is_refused_because_the_asyncpg_driver_rejects_it(
    make: Callable[..., Settings],
) -> None:
    """asyncpg takes `ssl`; SQLAlchemy passes `sslmode` straight through, and the first
    connection would then fail with a TypeError. Refuse it at startup with a clear message."""
    with pytest.raises(ValidationError, match=r"ssl=.*not sslmode"):
        make(database_url=f"{REMOTE}?sslmode=require")


def test_the_tls_rule_never_echoes_the_database_password() -> None:
    with pytest.raises(ValidationError) as caught:
        production_settings(database_url=REMOTE)

    assert "secret@" not in str(caught.value)


def test_a_field_error_never_echoes_the_database_password() -> None:
    """A startup failure ends up in the platform's logs, so it must not carry credentials.
    (A short URL, so pydantic's own truncation of long values can't hide a leak.)"""
    with pytest.raises(ValidationError, match="DATABASE_URL") as caught:
        make_settings(database_url="postgres://a:pw4242@h/d")

    assert "pw4242" not in str(caught.value)
    assert "input_value" not in str(caught.value)


def test_a_production_rule_error_never_echoes_the_secrets_it_checked() -> None:
    with pytest.raises(ValidationError, match="SESSION_SECRET") as caught:
        Settings(
            _env_file=None,
            app_env="production",
            database_url="postgresql+asyncpg://a:pw4242@localhost/d",
            session_secret="weak-secret",
        )

    message = str(caught.value)
    assert "weak-secret" not in message
    assert "pw4242" not in message
    assert "input_value" not in message


@pytest.mark.parametrize("value", [32 * 1024 - 1, 1024 * 1024 + 1])
def test_socket_max_message_bytes_bounds_are_refused(value: int) -> None:
    with pytest.raises(ValidationError, match="SOCKET_MAX_MESSAGE_BYTES"):
        make_settings(socket_max_message_bytes=value)


@pytest.mark.parametrize("value", [32 * 1024, 1024 * 1024])
def test_socket_max_message_bytes_bounds_are_accepted(value: int) -> None:
    assert make_settings(socket_max_message_bytes=value).socket_max_message_bytes == value
