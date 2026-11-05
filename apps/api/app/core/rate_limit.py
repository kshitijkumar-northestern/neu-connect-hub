"""In-memory sliding-window rate limits.

State lives in this process only, which is correct while the API runs as a single ASGI worker.
Moving to several workers means moving this to a shared store along with Socket.IO rooms.
"""

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_PRUNE_THRESHOLD = 10_000


@dataclass(frozen=True)
class Limit:
    max_events: int
    window_seconds: float


class RateLimiter:
    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        prune_threshold: int = DEFAULT_PRUNE_THRESHOLD,
    ) -> None:
        self._clock = clock
        # (bucket, key) -> (window seconds, event times)
        self._events: dict[tuple[str, str], tuple[float, deque[float]]] = {}
        self._threshold = prune_threshold
        self._prune_above = prune_threshold

    def __len__(self) -> int:
        return len(self._events)

    def check(self, bucket: str, key: str, limit: Limit) -> float | None:
        """Seconds until another event is allowed, or None if allowed now. Records nothing."""
        entry = self._events.get((bucket, key))
        if entry is None:
            return None
        events = entry[1]
        cutoff = self._clock() - limit.window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) < limit.max_events:
            return None
        return max(events[0] + limit.window_seconds - self._clock(), 0.0)

    def hit(self, bucket: str, key: str, limit: Limit) -> float | None:
        """Record an event, unless over the limit. Returns the retry delay when over."""
        retry_after = self.check(bucket, key, limit)
        if retry_after is not None:
            return retry_after
        _, events = self._events.setdefault((bucket, key), (limit.window_seconds, deque()))
        events.append(self._clock())
        if len(self._events) > self._prune_above:
            self._prune()
        return None

    def reset(self, bucket: str, key: str) -> None:
        self._events.pop((bucket, key), None)

    def forget(self, bucket: str, key_prefix: str) -> None:
        """Drop every key in a bucket that starts with key_prefix (rare; scans all keys)."""
        for entry in [k for k in self._events if k[0] == bucket and k[1].startswith(key_prefix)]:
            del self._events[entry]

    def _prune(self) -> None:
        """Drop keys whose newest event is outside their own window."""
        now = self._clock()
        stale = [
            key
            for key, (window, events) in self._events.items()
            if not events or events[-1] <= now - window
        ]
        for key in stale:
            del self._events[key]
        # Prune again only after the table doubles, so pruning stays amortised O(1) per hit.
        self._prune_above = max(self._threshold, 2 * len(self._events))
