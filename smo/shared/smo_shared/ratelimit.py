"""Token-bucket rate limiting (PR-SEC-8.2, shared state PR-SEC-8.5).

Each caller (the invoker id R1 Termination vouches for) has a bucket holding up to `burst` tokens that refills at
`rate` tokens a second; a request takes one. An empty bucket refuses the request, and says how long to wait for a
token (`Retry-After`). So a caller may burst up to `burst` requests and then continue at `rate` a second; one
runaway client cannot take the gateway from the others.

`TokenBuckets` keeps the buckets in the process: with N replicas each holds its own, so the budget a caller really has
is N x rate. `SharedTokenBuckets` (`R1_RATE_STORE=postgres`) keeps them in the `rate_bucket` table instead, so the N
replicas draw on one budget. Both have the same `take(caller)`; `docs/ARCHITECTURE.md` (process state) says which one
a deployment runs.

`rate <= 0` turns the limiter off. Idle buckets are forgotten once they would be full again, so the table stays
as small as the set of recently active callers.

The shared bucket, exactly (one statement per request):

    INSERT INTO rate_bucket (caller, tokens, refilled_at, last_allowed) VALUES (<new caller: a full bucket less this request>)
    ON CONFLICT (caller) DO UPDATE SET
        last_allowed = refilled >= 1, tokens = (refilled - 1 if refilled >= 1 else refilled), refilled_at = now
    RETURNING tokens, last_allowed
    where refilled = LEAST(burst, tokens + GREATEST(0, now - refilled_at) * rate)

so it is the same token bucket as the in-process one (burst, then `rate` a second; a refused request takes nothing), and the
refill arithmetic and the decision are one atomic statement: Postgres locks the caller's row for it, so replicas serialise per
caller and never lose an update, and no other caller is touched. Approximations, all deliberate:

  - `now` is the replica's own wall clock (`time.time()`), stored as epoch seconds. Replica clocks that differ by d seconds make
    a caller's refill jump by at most rate x d once; a clock that steps backwards refills nothing (the elapsed time is floored
    at 0). NTP-synchronised nodes (milliseconds) make this immaterial. The database clock is not used because SQLite, the unit
    tests' database, has no equivalent, and a limiter that cannot be tested is worse than a few milliseconds of skew.
  - `Retry-After` is worked out in the replica from the `tokens` the statement returned, as in memory.
  - Each request costs one round trip and one row update on the gateway's hot path, and the work runs in the thread pool, not on
    the event loop. The default stays `memory`.

When the database fails the limiter FAILS OPEN (the shared store only). A request is never refused because the limiter could not
be read: the limiter is a fairness control, and the gateway's authentication (which does not use this table) still runs. During
the outage each replica falls back to its own in-process bucket, so the budget degrades to N x rate rather than to unlimited,
the store is not tried again for `FAIL_BACKOFF_SECONDS` (so an outage puts neither a failed round trip nor a pool wait on every
request), and the failure is logged (at most one line in `LOG_INTERVAL_SECONDS`) and counted: `smo_rate_store_errors_total`
(statements that failed) and `smo_rate_store_fallbacks_total` (requests decided by the local bucket). Alert on either.
"""

import logging
import math
import os
import threading
import time
from collections.abc import Callable, Mapping

from sqlalchemy import Boolean, Float, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .metrics import record_rate_store_error, record_rate_store_fallback

log = logging.getLogger(__name__)

MAX_TRACKED_CALLERS = 10_000
FAIL_BACKOFF_SECONDS = 5.0                   # after a failed statement the shared store is not tried again for this long
PURGE_INTERVAL_SECONDS = 60.0                # how often a replica deletes the buckets that are full again
LOG_INTERVAL_SECONDS = 30.0                  # at most one "store unavailable" line this often


class RateBucket(Base):
    """One caller's bucket in the shared store (migration 0028). `refilled_at` is epoch seconds (see the module docstring)."""
    __tablename__ = "rate_bucket"

    caller: Mapped[str] = mapped_column(String, primary_key=True)
    tokens: Mapped[float] = mapped_column(Float, nullable=False)
    refilled_at: Mapped[float] = mapped_column(Float, nullable=False)
    last_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class TokenBuckets:
    blocking = False                                   # take() does no I/O: safe to call on the event loop

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


def _statements(dialect: str) -> tuple:
    """The upsert and the purge, with the dialect's names for the two-argument LEAST and GREATEST (Postgres has them; SQLite spells them min and max)."""
    least, greatest = ("LEAST", "GREATEST") if dialect == "postgresql" else ("min", "max")
    refilled = f"{least}(:burst, rate_bucket.tokens + {greatest}(0, :now - rate_bucket.refilled_at) * :rate)"
    # the only interpolated text is one of two fixed function names chosen above, never input: S608 does not apply
    take = text(
        "INSERT INTO rate_bucket (caller, tokens, refilled_at, last_allowed) VALUES (:caller, :first_tokens, :now, :first_allowed) "  # noqa: S608
        "ON CONFLICT (caller) DO UPDATE SET "
        f"last_allowed = ({refilled}) >= 1, "
        f"tokens = CASE WHEN ({refilled}) >= 1 THEN ({refilled}) - 1 ELSE ({refilled}) END, "
        "refilled_at = :now "
        "RETURNING tokens, last_allowed")
    purge = text(f"DELETE FROM rate_bucket WHERE tokens + {greatest}(0, :now - refilled_at) * :rate >= :burst")  # noqa: S608
    return take, purge


class SharedTokenBuckets:
    """The same token bucket as `TokenBuckets`, kept in the `rate_bucket` table so every replica draws on one budget (PR-SEC-8.5;
    semantics, approximations and the fail-open rule in the module docstring). `session_factory` is resolved at call time
    (default `smo_shared.db.SessionLocal`) so importing this opens nothing."""
    blocking = True                                    # take() is a database round trip: run it off the event loop

    def __init__(self, rate: Callable[[], float], burst: Callable[[], float], clock: Callable[[], float] = time.time,
                 session_factory=None, monotonic: Callable[[], float] = time.monotonic):
        self._rate, self._burst, self._clock, self._monotonic = rate, burst, clock, monotonic
        self._session_factory = session_factory
        self._local = TokenBuckets(rate, burst, monotonic)          # the fallback while the store is down
        self._down_until = 0.0
        self._last_purge = monotonic()
        self._last_log = float("-inf")
        self._lock = threading.Lock()

    def _session(self):
        if self._session_factory is not None:
            return self._session_factory()
        from . import db
        return db.SessionLocal()

    def take(self, caller: str) -> int | None:
        """None if the request may proceed, else the whole seconds to wait (at least 1). Never raises: a failing store falls back (fail open)."""
        rate, burst = float(self._rate()), float(self._burst())
        if rate <= 0:
            return None
        tick = self._monotonic()
        if tick < self._down_until:
            record_rate_store_fallback()
            return self._local.take(caller)
        try:
            tokens, allowed = self._take_shared(caller, rate, burst)
        except Exception as exc:                       # any failure of the store: the limiter fails open, see the docstring
            self._down_until = tick + FAIL_BACKOFF_SECONDS
            record_rate_store_error()
            record_rate_store_fallback()
            if tick - self._last_log >= LOG_INTERVAL_SECONDS:
                self._last_log = tick
                log.warning("rate limiter store unavailable, failing open to the per-replica bucket for %.0f s: %s: %s",
                            FAIL_BACKOFF_SECONDS, type(exc).__name__, exc)
            return self._local.take(caller)
        self._maybe_purge(tick, rate, burst)
        return None if allowed else max(1, math.ceil((1 - tokens) / rate))

    def _take_shared(self, caller: str, rate: float, burst: float) -> tuple[float, bool]:
        with self._session() as db:
            statement, _ = _statements(db.get_bind().dialect.name)
            first_allowed = burst >= 1
            row = db.execute(statement, {"caller": caller, "now": self._clock(), "rate": rate, "burst": burst,
                                         "first_tokens": burst - 1 if first_allowed else burst, "first_allowed": first_allowed}).one()
            db.commit()
        return float(row[0]), bool(row[1])

    def _maybe_purge(self, tick: float, rate: float, burst: float) -> None:
        """Delete the buckets that would be full again (what `TokenBuckets._forget_idle` does), at most once a PURGE_INTERVAL_SECONDS per replica.
        Idempotent, so replicas purging at once do no harm; a failure is ignored (the next interval retries; the table only grows meanwhile)."""
        with self._lock:
            if tick - self._last_purge < PURGE_INTERVAL_SECONDS:
                return
            self._last_purge = tick
        try:
            self.purge(rate, burst)
        except Exception:
            log.debug("rate limiter purge failed", exc_info=True)

    def purge(self, rate: float | None = None, burst: float | None = None) -> int:
        """Delete idle buckets now; returns how many went."""
        rate = float(self._rate()) if rate is None else rate
        burst = float(self._burst()) if burst is None else burst
        with self._session() as db:
            _, statement = _statements(db.get_bind().dialect.name)
            deleted = db.execute(statement, {"now": self._clock(), "rate": rate, "burst": burst}).rowcount
            db.commit()
        return deleted

    def clear(self) -> None:
        """Forget every bucket, in the store too (tests)."""
        self._local.clear()
        self._down_until = 0.0
        with self._session() as db:
            db.execute(text("DELETE FROM rate_bucket"))
            db.commit()

    def __len__(self) -> int:
        with self._session() as db:
            return int(db.execute(text("SELECT count(*) FROM rate_bucket")).scalar_one())


def store_from_environment(environ: Mapping[str, str] = os.environ) -> str:
    """`R1_RATE_STORE`: `memory` (default) or `postgres`. Anything else stops the process at start rather than silently limiting per replica."""
    value = environ.get("R1_RATE_STORE", "memory").strip().lower() or "memory"
    if value not in ("memory", "postgres"):
        raise RuntimeError(f"R1_RATE_STORE must be 'memory' or 'postgres', not {value!r}")
    return value
