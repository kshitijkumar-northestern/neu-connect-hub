from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from tests.conftest import API_ROOT, make_settings

STRONG_SECRET = "a-production-secret-with-more-than-32-characters"


def test_the_env_file_is_found_from_any_working_directory() -> None:
    env_file = Settings.model_config["env_file"]

    assert isinstance(env_file, Path)
    assert env_file.is_absolute()
    assert env_file == API_ROOT / ".env"


def test_production_rejects_placeholder_email_provider_keys() -> None:
    with pytest.raises(ValidationError, match="EMAIL_PROVIDER_API_KEY"):
        make_settings(
            app_env="production",
            session_secret=STRONG_SECRET,
            web_origin="https://hub.example",
            email_provider_api_key="replace_if_needed",
        )


def test_production_allows_an_unset_email_provider_key() -> None:
    settings = make_settings(
        app_env="production", session_secret=STRONG_SECRET, web_origin="https://hub.example"
    )

    assert settings.email_provider_api_key is None


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


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None, session_secret=STRONG_SECRET)  # type: ignore[call-arg]


def test_session_secret_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSION_SECRET", raising=False)

    with pytest.raises(ValidationError, match="session_secret"):
        Settings(_env_file=None, database_url="postgresql+asyncpg://u:p@localhost/db")  # type: ignore[call-arg]


def test_database_url_must_use_the_asyncpg_driver() -> None:
    with pytest.raises(ValidationError, match=r"postgresql\+asyncpg://"):
        make_settings(database_url="postgresql://app:local@localhost:5432/nu_hub")


@pytest.mark.parametrize("secret", ["replace_with_random_local_secret", "too-short"])
def test_production_rejects_placeholder_or_short_session_secrets(secret: str) -> None:
    with pytest.raises(ValidationError, match="SESSION_SECRET"):
        make_settings(app_env="production", session_secret=secret, web_origin="https://hub.example")


def test_production_accepts_a_strong_secret_and_https_origin() -> None:
    settings = make_settings(
        app_env="production", session_secret=STRONG_SECRET, web_origin="https://hub.example"
    )

    assert settings.is_production is True


def test_development_allows_the_placeholder_secret() -> None:
    settings = make_settings(
        app_env="development", session_secret="replace_with_random_local_secret"
    )

    assert settings.session_secret.get_secret_value() == "replace_with_random_local_secret"


def test_production_requires_an_https_web_origin() -> None:
    with pytest.raises(ValidationError, match="https"):
        make_settings(
            app_env="production", session_secret=STRONG_SECRET, web_origin="http://hub.example"
        )


def test_web_origin_drops_a_trailing_slash() -> None:
    assert make_settings(web_origin="http://localhost:8080/").web_origin == "http://localhost:8080"


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
    settings = make_settings(session_secret=STRONG_SECRET)

    assert STRONG_SECRET not in repr(settings)
    assert STRONG_SECRET not in str(settings.model_dump())
