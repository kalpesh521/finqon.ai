"""Small in-process sliding-window rate limiter (no Redis by design)."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable

from finqon.core.errors import RateLimitedError


class SlidingWindowRateLimiter:
    def __init__(
        self,
        max_events: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_events = max_events
        self._window = window_seconds
        self._clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        """Record one event for ``key`` or raise ``RateLimitedError``."""
        now = self._clock()
        events = self._events[key]
        while events and now - events[0] >= self._window:
            events.popleft()
        if len(events) >= self._max_events:
            retry_after = max(1, int(self._window - (now - events[0])) + 1)
            raise RateLimitedError(
                "Too many attempts. Try again later.",
                headers={"Retry-After": str(retry_after)},
            )
        events.append(now)
