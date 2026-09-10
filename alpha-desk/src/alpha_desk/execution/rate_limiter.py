"""Token-bucket rate limiter.

Mid-frequency trading has no reason to approach a broker's throttle. The
limiter is deliberately conservative and refuses rather than queues without
bound, so a runaway loop shows up as errors instead of a banned API key.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


class RateLimitExceeded(RuntimeError):
    pass


@dataclass
class RateLimiter:
    max_calls: int
    per_seconds: float
    _calls: deque[float] = field(default_factory=deque, repr=False)

    def _prune(self, now: float) -> None:
        cutoff = now - self.per_seconds
        while self._calls and self._calls[0] <= cutoff:
            self._calls.popleft()

    def allow(self, now: float | None = None) -> bool:
        t = time.monotonic() if now is None else now
        self._prune(t)
        if len(self._calls) >= self.max_calls:
            return False
        self._calls.append(t)
        return True

    def acquire(self, now: float | None = None) -> None:
        if not self.allow(now):
            raise RateLimitExceeded(
                f"rate limit hit: {self.max_calls} calls per {self.per_seconds}s"
            )

    @property
    def in_window(self) -> int:
        self._prune(time.monotonic())
        return len(self._calls)
