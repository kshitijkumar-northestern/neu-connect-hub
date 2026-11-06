import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, EmailStr, Field

from app.modules.auth.models import User

# NIST SP 800-63B: length over composition rules. The upper bound caps hashing work per request.
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128


def _strip(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


# Trimmed and lowercased, so "  Ada@Northeastern.edu " and "ada@northeastern.edu" are one account.
NormalizedEmail = Annotated[EmailStr, BeforeValidator(_strip), AfterValidator(str.lower)]
NewPassword = Annotated[str, Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)]
LinkToken = Annotated[str, Field(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterRequest(_Request):
    email: NormalizedEmail
    password: NewPassword


class EmailRequest(_Request):
    email: NormalizedEmail


class LoginRequest(_Request):
    email: NormalizedEmail
    password: Annotated[str, Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)]


class TokenRequest(_Request):
    token: LinkToken


class PasswordResetConfirm(_Request):
    token: LinkToken
    password: NewPassword


class StatusResponse(BaseModel):
    status: str


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    verified: bool
    created_at: datetime

    @classmethod
    def of(cls, user: User) -> "UserResponse":
        return cls(
            id=user.id,
            email=user.email,
            role=user.role,
            verified=user.verified_at is not None,
            created_at=user.created_at,
        )
