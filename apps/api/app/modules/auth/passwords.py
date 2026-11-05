import asyncio

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings


class PasswordService:
    """Argon2id hashing, run in a worker thread so it never blocks the event loop."""

    def __init__(self, settings: Settings) -> None:
        self._hasher = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_cost_kib,
            parallelism=settings.argon2_parallelism,
        )
        # Verified against when the email is unknown, so both paths cost about the same.
        self._dummy_hash = self._hasher.hash("timing-equaliser")

    async def hash(self, password: str) -> str:
        return await asyncio.to_thread(self._hasher.hash, password)

    async def verify(self, password_hash: str | None, password: str) -> bool:
        """True only for a real hash that matches; unknown accounts always fail."""
        target = password_hash if password_hash is not None else self._dummy_hash
        try:
            await asyncio.to_thread(self._hasher.verify, target, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
        return password_hash is not None

    def needs_rehash(self, password_hash: str) -> bool:
        return self._hasher.check_needs_rehash(password_hash)
