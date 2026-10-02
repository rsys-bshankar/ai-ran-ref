"""Token-bucket rate limiting (PR-SEC-8.2).

Each caller (the invoker id R1 Termination vouches for) has a bucket holding up to `burst` tokens that refills at
`rate` tokens a second; a request takes one. An empty bucket refuses the request, and says how long to wait for a
token (`Retry-After`). So a caller may burst up to `burst` requests and then continue at `rate` a second; one
runaway client cannot take the gateway from the others.

The buckets live in the process: with N replicas each holds its own, so the budget a caller really has is
N x rate until the shared store of `SEC-8.5` exists. `docs/ARCHITECTURE.md` (process state) says so.

`rate <= 0` turns the limiter off. Idle buckets are forgotten once they would be full again, so the table stays
as small as the set of recently active callers.
"""

import math
import threading
import time
from collections.abc import Callable

MAX_TRACKED_CALLERS = 10_000


class TokenBuckets:
    def __init__(self, rate: Callable[[], float], burst: Callable[[], float], clock: Callable[[], float] = time.monotonic):
        self._rate, self._burst, self._clock = rate, burst, clock
        self._buckets: dict[str, tuple[float, float]] = {}   # caller -> (tokens, last refill)
        self._lock = threading.Lock()

    def take(self, caller: str) -> int | None:
        """None if the request may proceed, else the whole seconds to wait (at least 1)."""
        rate, burst = float(self._rate()), float(self._burst())
        if rate <= 0:
            return None
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(caller, (burst, now))
            tokens = min(burst, tokens + (now - last) * rate)
            if tokens >= 1:
                self._buckets[caller] = (tokens - 1, now)
                allowed = None
            else:
                self._buckets[caller] = (tokens, now)
                allowed = max(1, math.ceil((1 - tokens) / rate))
            if len(self._buckets) > MAX_TRACKED_CALLERS:
                self._forget_idle(now, rate, burst)
            return allowed

    def _forget_idle(self, now: float, rate: float, burst: float) -> None:
        for caller in [c for c, (tokens, last) in self._buckets.items() if tokens + (now - last) * rate >= burst]:
            del self._buckets[caller]

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()

    def __len__(self) -> int:
        return len(self._buckets)
