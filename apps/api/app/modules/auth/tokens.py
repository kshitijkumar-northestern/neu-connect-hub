import hashlib
import secrets

TOKEN_BYTES = 32  # 256 bits of randomness -> 43 URL-safe characters


def new_token() -> tuple[str, str]:
    """A fresh random token and the SHA-256 digest that is the only thing stored."""
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    # A fast hash is right here: the token is high-entropy random data, not a password.
    return hashlib.sha256(raw.encode()).hexdigest()
