"""Pure derivation of Scan to Connect tokens: no database, no clock, so it's unit-testable on
its own and the service can lean on it without a round trip."""

import base64
import hashlib
import hmac
import re
import uuid
from typing import TypeGuard

# HMAC-SHA256 is 32 bytes; base64url of 32 bytes is 44 characters with one '=' pad, dropped here.
TOKEN_LENGTH = 43
_TOKEN_FORMAT = re.compile(rf"^[A-Za-z0-9_-]{{{TOKEN_LENGTH}}}$")


def token_for(key: bytes, row_id: uuid.UUID) -> str:
    """The link's token: HMAC-SHA256(key, row id), base64url with no padding. Deterministic, so
    the server can show the same link again from the row alone; the token itself is never stored."""
    digest = hmac.new(key, row_id.bytes, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def hash_token(token: str) -> str:
    """The only form of the token kept in the database."""
    return hashlib.sha256(token.encode()).hexdigest()


def is_well_formed(token: object) -> TypeGuard[str]:
    """Checked before any database lookup, so a missing, wrongly-typed or malformed token never
    reaches a query. The JSON body of `POST /connect/resolve` can carry any value here."""
    return isinstance(token, str) and bool(_TOKEN_FORMAT.fullmatch(token))
