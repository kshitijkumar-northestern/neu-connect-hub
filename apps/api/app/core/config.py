from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

API_ROOT = Path(__file__).resolve().parents[2]
# apps/api/.env, wherever the process was started from (repo root, apps/api, CI).
ENV_FILE = API_ROOT / ".env"

# Values shipped in apps/api/.env.example. They are fine locally and refused in production.
PLACEHOLDER_SECRETS = frozenset({"replace_with_random_local_secret", "replace_if_needed"})
MIN_PRODUCTION_SECRET_LENGTH = 32
MAX_TURN_CREDENTIAL_TTL_SECONDS = 3600
MAX_ROOM_CAPACITY = 8
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
    # Request bodies larger than this get 413 before any parsing. A full profile is under 16 KiB.
    max_request_body_bytes: int = 64 * 1024

    # Registration is limited to these campus email domains (comma-separated).
    allowed_email_domains: str = ""

    # Mail: "dev" writes messages to dev_mail_dir (never to logs); "smtp" sends through a provider.
    mail_backend: Literal["dev", "smtp"] = "dev"
    mail_from: str = "NU Connect Hub <no-reply@localhost>"
    dev_mail_dir: Path = API_ROOT / ".dev-mail"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None  # the password is EMAIL_PROVIDER_API_KEY

    # Sessions and single-use tokens.
    session_idle_timeout_seconds: int = 7 * 24 * 3600
    session_absolute_lifetime_seconds: int = 30 * 24 * 3600
    verification_token_ttl_seconds: int = 24 * 3600
    password_reset_token_ttl_seconds: int = 3600
    # Set to the registrable domain (e.g. "hub.example") when web and API are on sibling hosts,
    # so the web app can read the CSRF cookie. Empty means host-only cookies.
    cookie_domain: str | None = None

    # Calls. The browser mesh doesn't scale past a handful of people, so rooms are capped. STUN and
    # TURN URLs are comma-separated (e.g. "turn:turn.hub.example:3478?transport=udp"); TURN uses the
    # coturn REST-API scheme, where TURN_SECRET signs short-lived credentials (at most an hour).
    room_capacity: int = 4
    stun_urls: str = ""
    turn_urls: str = ""
    turn_secret: SecretStr | None = None
    turn_credential_ttl_seconds: int = 3600

    # Argon2id cost. The defaults match argon2-cffi's RFC 9106 low-memory profile; tests lower them.
    argon2_time_cost: int = 3
    argon2_memory_cost_kib: int = 65536
    argon2_parallelism: int = 4

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def allowed_domains(self) -> frozenset[str]:
        return frozenset(
            domain.strip().lower()
            for domain in self.allowed_email_domains.split(",")
            if domain.strip()
        )

    @property
    def stun_url_list(self) -> list[str]:
        return [url.strip() for url in self.stun_urls.split(",") if url.strip()]

    @property
    def turn_url_list(self) -> list[str]:
        return [url.strip() for url in self.turn_urls.split(",") if url.strip()]

    @property
    def cookie_secure(self) -> bool:
        """Secure cookies whenever the site is served over https (always, in production)."""
        return self.web_origin.startswith("https://")

    @field_validator("database_url")
    @classmethod
    def _asyncpg_driver(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must start with postgresql+asyncpg://")
        return value

    @field_validator("turn_credential_ttl_seconds")
    @classmethod
    def _short_lived_turn(cls, value: int) -> int:
        if not 60 <= value <= MAX_TURN_CREDENTIAL_TTL_SECONDS:
            raise ValueError(
                f"TURN_CREDENTIAL_TTL_SECONDS must be 60-{MAX_TURN_CREDENTIAL_TTL_SECONDS}"
            )
        return value

    @field_validator("room_capacity")
    @classmethod
    def _small_rooms(cls, value: int) -> int:
        if not 2 <= value <= MAX_ROOM_CAPACITY:
            raise ValueError(f"ROOM_CAPACITY must be 2-{MAX_ROOM_CAPACITY}")
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
        if self.mail_backend != "smtp" or not self.smtp_host:
            raise ValueError("MAIL_BACKEND must be smtp with SMTP_HOST set in production")
        if self.turn_url_list:
            turn_secret = self.turn_secret.get_secret_value() if self.turn_secret else ""
            if (
                turn_secret in PLACEHOLDER_SECRETS
                or len(turn_secret) < MIN_PRODUCTION_SECRET_LENGTH
            ):
                raise ValueError(
                    f"TURN_SECRET must be a random value of at least "
                    f"{MIN_PRODUCTION_SECRET_LENGTH} characters when TURN_URLS is set in production"
                )
        if not self.allowed_domains:
            raise ValueError(
                "ALLOWED_EMAIL_DOMAINS must list at least one campus domain in production"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    # Required fields come from the environment or apps/api/.env.
    return Settings()
