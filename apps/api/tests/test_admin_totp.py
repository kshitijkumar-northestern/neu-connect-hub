"""Pure TOTP math: RFC 6238 vectors, truncation, the clock-drift window and the provisioning URI.

No database and no app fixtures: everything here is a plain function of its arguments.
"""

import base64
import uuid
from datetime import UTC, datetime
from urllib.parse import unquote

import pytest

from app.modules.admin.totp import (
    code_at,
    derive_secret,
    matching_step,
    provisioning_uri,
    step_for,
    to_base32,
)

# RFC 6238 Appendix B: SHA-1, 8-digit TOTP over the 20-byte ASCII secret "12345678901234567890".
RFC_6238_SECRET = b"12345678901234567890"
RFC_6238_VECTORS = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]


@pytest.mark.parametrize(("unix_time", "expected"), RFC_6238_VECTORS)
def test_rfc_6238_sha1_vectors_at_8_digits(unix_time: int, expected: str) -> None:
    assert code_at(RFC_6238_SECRET, unix_time // 30, digits=8) == expected


def test_code_at_defaults_to_6_digits() -> None:
    code = code_at(RFC_6238_SECRET, 1)

    assert len(code) == 6
    assert code.isdigit()


def test_code_at_is_zero_padded() -> None:
    # Some step produces a truncated value under 100000; the code must still be 6 characters.
    codes = {code_at(RFC_6238_SECRET, step) for step in range(200)}
    assert all(len(code) == 6 for code in codes)


# --- secret derivation and encoding -----------------------------------------------------------


def test_derive_secret_is_160_bits() -> None:
    secret = derive_secret(b"a-32-byte-or-longer-mfa-key-----", uuid.uuid4(), b"0" * 16)

    assert len(secret) == 20


def test_derive_secret_is_deterministic_and_keyed_by_user_and_nonce() -> None:
    key = b"a-32-byte-or-longer-mfa-key-----"
    user_id = uuid.uuid4()
    nonce = b"\x01" * 16

    assert derive_secret(key, user_id, nonce) == derive_secret(key, user_id, nonce)
    assert derive_secret(key, user_id, nonce) != derive_secret(key, uuid.uuid4(), nonce)
    assert derive_secret(key, user_id, nonce) != derive_secret(key, user_id, b"\x02" * 16)
    other_key = b"a-different-key-32-bytes-------"
    assert derive_secret(key, user_id, nonce) != derive_secret(other_key, user_id, nonce)


def test_base32_encoding_has_no_padding_and_round_trips() -> None:
    secret = derive_secret(b"a-32-byte-or-longer-mfa-key-----", uuid.uuid4(), b"\xaa" * 16)

    encoded = to_base32(secret)

    assert "=" not in encoded
    padded = encoded + "=" * (-len(encoded) % 8)
    assert base64.b32decode(padded) == secret


# --- provisioning URI -----------------------------------------------------------------------


def test_provisioning_uri_has_the_expected_shape() -> None:
    secret = derive_secret(b"a-32-byte-or-longer-mfa-key-----", uuid.uuid4(), b"\xbb" * 16)

    uri = provisioning_uri(secret, "ada@northeastern.edu")

    assert uri.startswith("otpauth://totp/NU%20Connect%20Hub:ada%40northeastern.edu?")
    assert f"secret={to_base32(secret)}" in uri
    assert "issuer=NU%20Connect%20Hub" in uri
    assert "algorithm=SHA1" in uri
    assert "digits=6" in uri
    assert "period=30" in uri
    assert unquote(uri) == (
        f"otpauth://totp/NU Connect Hub:ada@northeastern.edu?secret={to_base32(secret)}"
        "&issuer=NU Connect Hub&algorithm=SHA1&digits=6&period=30"
    )


# --- verification window ---------------------------------------------------------------------

SECRET = derive_secret(b"a-32-byte-or-longer-mfa-key-----", uuid.uuid4(), b"\xcc" * 16)
NOW = datetime(2024, 1, 1, tzinfo=UTC)


def test_matching_step_accepts_the_current_step() -> None:
    current = step_for(NOW)

    assert matching_step(SECRET, code_at(SECRET, current), NOW) == current


def test_matching_step_accepts_one_step_either_side_for_clock_drift() -> None:
    current = step_for(NOW)

    assert matching_step(SECRET, code_at(SECRET, current - 1), NOW) == current - 1
    assert matching_step(SECRET, code_at(SECRET, current + 1), NOW) == current + 1


def test_matching_step_rejects_steps_outside_the_window() -> None:
    current = step_for(NOW)

    assert matching_step(SECRET, code_at(SECRET, current - 2), NOW) is None
    assert matching_step(SECRET, code_at(SECRET, current + 2), NOW) is None


def test_matching_step_rejects_a_wrong_code() -> None:
    assert matching_step(SECRET, "000000", NOW) is None


@pytest.mark.parametrize(
    "malformed",
    [
        "12345é",  # non-ASCII: must never reach hmac.compare_digest and crash
        "abcdef",  # right length, not digits
        "12345",  # one digit short
        "1234567",  # one digit long
        "",  # empty
    ],
)
def test_matching_step_rejects_malformed_codes_without_crashing(malformed: str) -> None:
    assert matching_step(SECRET, malformed, NOW) is None
