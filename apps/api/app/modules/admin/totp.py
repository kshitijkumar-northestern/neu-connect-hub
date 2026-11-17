"""TOTP (RFC 6238, over RFC 4226 HOTP): deriving an admin's secret from the database row and
checking a submitted code against it. Pure functions only; nothing here touches the database.

The secret itself is never stored. It is re-derived on every check from MFA_SECRET_KEY and the
16-byte nonce held in `admin_mfa.nonce`, so a copy of the database alone reveals nothing.
"""

import base64
import hashlib
import hmac
import uuid
from datetime import datetime
from urllib.parse import quote, urlencode

ISSUER = "NU Connect Hub"
SECRET_BYTES = 20  # 160 bits, RFC 4226's recommended HOTP secret length
STEP_SECONDS = 30
DEFAULT_DIGITS = 6
# Clock drift tolerance: the step before and after the caller's current one are also accepted.
WINDOW = 1


def derive_secret(mfa_key: bytes, user_id: uuid.UUID, nonce: bytes) -> bytes:
    """The admin's TOTP secret: HMAC-SHA256(MFA key, user id || nonce), truncated to 160 bits."""
    return hmac.new(mfa_key, user_id.bytes + nonce, hashlib.sha256).digest()[:SECRET_BYTES]


def to_base32(secret: bytes) -> str:
    """Base32 for display and for pasting into an authenticator app, without padding."""
    return base64.b32encode(secret).decode("ascii").rstrip("=")


def provisioning_uri(secret: bytes, email: str) -> str:
    """The otpauth:// URI an authenticator app's QR scanner expects."""
    label = f"{quote(ISSUER, safe='')}:{quote(email, safe='')}"
    params = urlencode(
        {
            "secret": to_base32(secret),
            "issuer": ISSUER,
            "algorithm": "SHA1",
            "digits": DEFAULT_DIGITS,
            "period": STEP_SECONDS,
        },
        quote_via=quote,
    )
    return f"otpauth://totp/{label}?{params}"


def code_at(secret: bytes, step: int, digits: int = DEFAULT_DIGITS) -> str:
    """The RFC 4226 HOTP value for a given counter (here, a 30-second time step)."""
    counter = step.to_bytes(8, "big")
    mac = hmac.new(secret, counter, hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    truncated = int.from_bytes(mac[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(truncated % (10**digits)).zfill(digits)


def step_for(now: datetime, *, step_seconds: int = STEP_SECONDS) -> int:
    return int(now.timestamp() // step_seconds)


def is_well_formed(code: str, *, digits: int = DEFAULT_DIGITS) -> bool:
    """A submitted code must be exactly `digits` ASCII digits. Checked before any comparison:
    `hmac.compare_digest` raises `TypeError` for a non-ASCII string, and a wrong shape must fail
    exactly like a wrong code, not crash or stand out as a different kind of rejection."""
    return len(code) == digits and code.isascii() and code.isdigit()


def matching_step(
    secret: bytes,
    code: str,
    now: datetime,
    *,
    digits: int = DEFAULT_DIGITS,
    window: int = WINDOW,
) -> int | None:
    """The step a submitted code matches (current, or one step either side for clock drift), or
    None. The caller applies replay protection: the returned step must be strictly newer than the
    last one accepted, checked by a single conditional UPDATE so two concurrent checks can't both
    succeed."""
    if not is_well_formed(code, digits=digits):
        return None
    current = step_for(now)
    for offset in range(-window, window + 1):
        candidate = current + offset
        if hmac.compare_digest(code_at(secret, candidate, digits), code):
            return candidate
    return None
