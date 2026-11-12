import hashlib
import hmac
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import MAX_SHARE_TOKEN_TTL_SECONDS, Settings
from tests.conftest import (
    API_ROOT,
    PRODUCTION_SECRET,
    TEST_SESSION_SECRET,
    make_settings,
    production_settings,
)


def test_the_env_file_is_found_from_any_working_directory() -> None:
    env_file = Settings.model_config["env_file"]

    assert isinstance(env_file, Path)
    assert env_file.is_absolute()
    assert env_file == API_ROOT / ".env"


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None, session_secret=PRODUCTION_SECRET)  # type: ignore[call-arg]


def test_session_secret_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSION_SECRET", raising=False)

    with pytest.raises(ValidationError, match="session_secret"):
        Settings(_env_file=None, database_url="postgresql+asyncpg://u:p@localhost/db")  # type: ignore[call-arg]


def test_database_url_must_use_the_asyncpg_driver() -> None:
    with pytest.raises(ValidationError, match=r"postgresql\+asyncpg://"):
        make_settings(database_url="postgresql://app:local@localhost:5432/nu_hub")


def test_complete_production_settings_are_accepted() -> None:
    settings = production_settings()

    assert settings.is_production is True
    assert settings.cookie_secure is True


@pytest.mark.parametrize("secret", ["replace_with_random_local_secret", "too-short"])
def test_production_rejects_placeholder_or_short_session_secrets(secret: str) -> None:
    with pytest.raises(ValidationError, match="SESSION_SECRET"):
        production_settings(session_secret=secret)


def test_production_rejects_placeholder_email_provider_keys() -> None:
    with pytest.raises(ValidationError, match="EMAIL_PROVIDER_API_KEY"):
        production_settings(email_provider_api_key="replace_if_needed")


def test_production_allows_an_unset_email_provider_key() -> None:
    assert production_settings().email_provider_api_key is None


def test_production_requires_an_https_web_origin() -> None:
    with pytest.raises(ValidationError, match="https"):
        production_settings(web_origin="http://hub.example")


@pytest.mark.parametrize("overrides", [{"mail_backend": "dev"}, {"smtp_host": None}])
def test_production_refuses_the_dev_mailer_or_a_missing_smtp_host(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="MAIL_BACKEND"):
        production_settings(**overrides)


def test_production_requires_allowed_email_domains() -> None:
    with pytest.raises(ValidationError, match="ALLOWED_EMAIL_DOMAINS"):
        production_settings(allowed_email_domains=" , ")


def test_development_allows_the_placeholder_secret_and_dev_mailer() -> None:
    settings = make_settings(
        app_env="development", session_secret="replace_with_random_local_secret"
    )

    assert settings.session_secret.get_secret_value() == "replace_with_random_local_secret"
    assert settings.mail_backend == "dev"
    assert settings.cookie_secure is False


def test_allowed_email_domains_are_trimmed_lowercased_and_deduplicated() -> None:
    settings = make_settings(
        allowed_email_domains=" Northeastern.edu, husky.neu.edu,,northeastern.edu "
    )

    assert settings.allowed_domains == frozenset({"northeastern.edu", "husky.neu.edu"})


def test_web_origin_drops_a_trailing_slash() -> None:
    assert make_settings(web_origin="http://localhost:8080/").web_origin == "http://localhost:8080"


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("HTTPS://Hub.Example:443/", "https://hub.example"),
        ("http://Example.com:80", "http://example.com"),
        ("http://localhost:8080", "http://localhost:8080"),
        ("https://hub.example:8443", "https://hub.example:8443"),
        ("http://[::1]:8080", "http://[::1]:8080"),
    ],
)
def test_web_origin_is_normalised_like_a_browser_origin_header(
    configured: str, expected: str
) -> None:
    assert make_settings(web_origin=configured).web_origin == expected


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:8080/app",
        "localhost:8080",
        "ftp://files.example",
        "http://x/?a=1",
        "http://user:pass@hub.example",
        "http://hub.example:notaport",
    ],
)
def test_web_origin_must_be_a_bare_http_origin(origin: str) -> None:
    with pytest.raises(ValidationError, match="WEB_ORIGIN"):
        make_settings(web_origin=origin)


def test_secrets_are_hidden_from_repr() -> None:
    settings = make_settings(session_secret=PRODUCTION_SECRET)

    assert PRODUCTION_SECRET not in repr(settings)
    assert PRODUCTION_SECRET not in str(settings.model_dump())


@pytest.mark.parametrize("value", [59, 3601])
def test_turn_credential_ttl_seconds_bounds_are_refused(value: int) -> None:
    with pytest.raises(ValidationError, match="TURN_CREDENTIAL_TTL_SECONDS"):
        make_settings(turn_credential_ttl_seconds=value)


@pytest.mark.parametrize("value", [60, 3600])
def test_turn_credential_ttl_seconds_bounds_are_accepted(value: int) -> None:
    assert make_settings(turn_credential_ttl_seconds=value).turn_credential_ttl_seconds == value


@pytest.mark.parametrize("value", [1, 9])
def test_room_capacity_bounds_are_refused(value: int) -> None:
    with pytest.raises(ValidationError, match="ROOM_CAPACITY"):
        make_settings(room_capacity=value)


@pytest.mark.parametrize("value", [2, 8])
def test_room_capacity_bounds_are_accepted(value: int) -> None:
    assert make_settings(room_capacity=value).room_capacity == value


def test_production_refuses_turn_urls_without_a_strong_turn_secret() -> None:
    with pytest.raises(ValidationError, match="TURN_SECRET"):
        production_settings(turn_urls="turn:turn.example:3478", turn_secret="too-short")


def test_production_refuses_turn_urls_with_the_placeholder_turn_secret() -> None:
    with pytest.raises(ValidationError, match="TURN_SECRET"):
        production_settings(turn_urls="turn:turn.example:3478", turn_secret="replace_if_needed")


def test_production_allows_turn_urls_with_a_strong_turn_secret() -> None:
    settings = production_settings(
        turn_urls="turn:turn.example:3478", turn_secret=PRODUCTION_SECRET
    )
    assert settings.turn_url_list == ["turn:turn.example:3478"]


def test_production_allows_no_turn_urls_with_no_turn_secret() -> None:
    assert production_settings().turn_url_list == []


@pytest.mark.parametrize("secret", [None, "replace_with_random_local_secret", "too-short"])
def test_production_rejects_missing_placeholder_or_short_share_token_secrets(
    secret: str | None,
) -> None:
    with pytest.raises(ValidationError, match="SHARE_TOKEN_SECRET"):
        production_settings(share_token_secret=secret)


def test_production_rejects_a_share_token_secret_equal_to_the_session_secret() -> None:
    with pytest.raises(ValidationError, match="SHARE_TOKEN_SECRET"):
        production_settings(share_token_secret=PRODUCTION_SECRET)


def test_share_token_key_falls_back_to_a_key_derived_from_session_secret_outside_production() -> (
    None
):
    settings = make_settings()

    assert settings.share_token_secret is None
    assert (
        settings.share_token_key
        == hmac.new(TEST_SESSION_SECRET.encode(), b"share-token", hashlib.sha256).digest()
    )


def test_share_token_key_uses_the_configured_secret_when_set() -> None:
    settings = make_settings(share_token_secret="a-distinct-local-share-secret")

    assert settings.share_token_key == b"a-distinct-local-share-secret"


@pytest.mark.parametrize("value", [3599, MAX_SHARE_TOKEN_TTL_SECONDS + 1])
def test_share_token_ttl_seconds_bounds_are_refused(value: int) -> None:
    with pytest.raises(ValidationError, match="SHARE_TOKEN_TTL_SECONDS"):
        make_settings(share_token_ttl_seconds=value)


@pytest.mark.parametrize("value", [3600, MAX_SHARE_TOKEN_TTL_SECONDS])
def test_share_token_ttl_seconds_bounds_are_accepted(value: int) -> None:
    assert make_settings(share_token_ttl_seconds=value).share_token_ttl_seconds == value
