"""Recovery codes for an admin who has lost their authenticator. Pure functions only: the service
stores only `hash_code`'s output, never a code itself."""

import hashlib
import re
import secrets

# Excludes 0/O and 1/I, so a handwritten or misread code is never ambiguous.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_COUNT = 10
CODE_LENGTH = 12
GROUP_SIZE = 4
_STRIPPED = re.compile(r"[-\s]")


def generate_codes(count: int = CODE_COUNT) -> list[str]:
    """Freshly random codes, formatted as shown to the admin (e.g. "XK4R-7NGQ-2P9W")."""
    return [_format(_raw_code()) for _ in range(count)]


def normalize(code: str) -> str:
    """Uppercase, with the dashes and any whitespace the admin may have pasted stripped."""
    return _STRIPPED.sub("", code).upper()


def hash_code(code: str) -> str:
    """The SHA-256 hex digest of the normalised code: the only form ever stored."""
    return hashlib.sha256(normalize(code).encode()).hexdigest()


def _raw_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def _format(raw: str) -> str:
    return "-".join(raw[i : i + GROUP_SIZE] for i in range(0, len(raw), GROUP_SIZE))
