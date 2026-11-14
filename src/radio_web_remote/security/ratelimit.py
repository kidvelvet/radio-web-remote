"""In-memory sliding-window rate limit, per key (client address for logins).

Complements the per-account lockout in users.py: lockout protects one
account from many guesses; this protects the server from one address
guessing many accounts.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

MAX_KEYS = 10_000
SWEEP_EVERY_S = 5.0


class RateLimiter:
    def __init__(
        self, max_events: int, window_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.max_events = max_events
        self.window_s = window_s
        self._clock = clock
        self._events: dict[str, deque[float]] = {}
        self._next_sweep = 0.0

    def _trim(self, key: str, now: float) -> deque[float]:
        q = self._events.setdefault(key, deque())
        while q and now - q[0] > self.window_s:
            q.popleft()
        return q

    def allow(self, key: str) -> bool:
        """Record an attempt; False if this key is over its limit."""
        now = self._clock()
        q = self._trim(key, now)
        if len(q) >= self.max_events:
            return False
        q.append(now)
        if len(self._events) > MAX_KEYS and now >= self._next_sweep:
            # Bound memory under a flood of addresses, without rescanning on every call.
            self._next_sweep = now + SWEEP_EVERY_S
            for k in list(self._events):
                if not self._trim(k, now):
                    del self._events[k]
            while len(self._events) > MAX_KEYS:  # still too many: drop the oldest keys
                del self._events[next(iter(self._events))]
        return True

    def refund(self, key: str) -> None:
        """Give back the most recent attempt (a successful login does not count)."""
        q = self._events.get(key)
        if q:
            q.pop()

    def retry_after(self, key: str) -> int:
        q = self._trim(key, self._clock())
        if len(q) < self.max_events:
            return 0
        return int(self.window_s - (self._clock() - q[0])) + 1
