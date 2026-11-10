from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/api/.env, wherever the process was started from (repo root, apps/api, CI).
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

# Values shipped in apps/api/.env.example. They are fine locally and refused in production.
PLACEHOLDER_SECRETS = frozenset({"replace_with_random_local_secret", "replace_if_needed"})
MIN_PRODUCTION_SECRET_LENGTH = 32
_DEFAULT_PORTS = {"http": 80, "https": 443}
_ORIGIN_ERROR = "WEB_ORIGIN must be a bare origin such as https://hub.example"


class Settings(BaseSettings):
    """Runtime configuration, read from the environment and apps/api/.env."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str
    session_secret: SecretStr
    web_origin: str = "http://localhost:8080"
    email_provider_api_key: SecretStr | None = None
    embedding_model_id: str = "sentence-transformers/all-MiniLM-L6-v2"

    db_connect_timeout_seconds: float = 5.0
    readiness_timeout_seconds: float = 2.0

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @field_validator("database_url")
    @classmethod
    def _asyncpg_driver(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must start with postgresql+asyncpg://")
        return value

    @field_validator("web_origin")
    @classmethod
    def _bare_origin(cls, value: str) -> str:
        """Normalise to the exact form browsers send in the Origin header.

        Socket.IO and CORS compare origins as strings, so the host is lowercased and a
        default port (:80 for http, :443 for https) is dropped.
        """
        parts = urlsplit(value)
        if (
            parts.scheme not in _DEFAULT_PORTS
            or not parts.hostname
            or parts.username is not None
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
        ):
            raise ValueError(_ORIGIN_ERROR)
        try:
            port = parts.port
        except ValueError as exc:
            raise ValueError(_ORIGIN_ERROR) from exc
        host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
        if port is not None and port != _DEFAULT_PORTS[parts.scheme]:
            host = f"{host}:{port}"
        return f"{parts.scheme}://{host}"

    @model_validator(mode="after")
    def _production_rules(self) -> Self:
        if not self.is_production:
            return self
        secret = self.session_secret.get_secret_value()
        if secret in PLACEHOLDER_SECRETS or len(secret) < MIN_PRODUCTION_SECRET_LENGTH:
            raise ValueError(
                f"SESSION_SECRET must be a random value of at least "
                f"{MIN_PRODUCTION_SECRET_LENGTH} characters in production"
            )
        email_key = self.email_provider_api_key
        if email_key is not None and email_key.get_secret_value() in PLACEHOLDER_SECRETS:
            raise ValueError("EMAIL_PROVIDER_API_KEY is still the placeholder value in production")
        if not self.web_origin.startswith("https://"):
            raise ValueError("WEB_ORIGIN must use https in production")
        return self


@lru_cache
def get_settings() -> Settings:
    # Required fields come from the environment or apps/api/.env.
    return Settings()
