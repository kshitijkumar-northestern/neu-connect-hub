"""Who is online, counted per process (correct while the API runs as a single ASGI worker)."""

import uuid
from collections import Counter


class Presence:
    """Open sockets per student. Several tabs count as one presence."""

    def __init__(self) -> None:
        self._sockets: Counter[uuid.UUID] = Counter()

    def joined(self, user_id: uuid.UUID) -> bool:
        """Records a socket; True when the student has just come online."""
        self._sockets[user_id] += 1
        return self._sockets[user_id] == 1

    def left(self, user_id: uuid.UUID) -> bool:
        """Forgets a socket; True when the student has just gone offline."""
        if self._sockets[user_id] <= 1:
            del self._sockets[user_id]
            return True
        self._sockets[user_id] -= 1
        return False

    def is_online(self, user_id: uuid.UUID) -> bool:
        return self._sockets[user_id] > 0
