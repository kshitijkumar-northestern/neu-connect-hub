from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

CodeField = Field(min_length=1, max_length=12)
RecoveryCodeField = Field(default=None, min_length=1, max_length=32)


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfirmRequest(_Request):
    code: str = CodeField


class VerifyRequest(_Request):
    """Exactly one of `code` (the authenticator's 6-digit code) or `recovery_code`."""

    code: str | None = Field(default=None, min_length=1, max_length=12)
    recovery_code: str | None = RecoveryCodeField

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.code is None) == (self.recovery_code is None):
            raise ValueError("Provide exactly one of code or recovery_code.")
        return self


class MfaStatus(BaseModel):
    enrolled: bool
    verified: bool
    recovery_codes_left: int


class EnrollResponse(BaseModel):
    secret: str
    otpauth_url: str


class ConfirmResponse(BaseModel):
    recovery_codes: list[str]
