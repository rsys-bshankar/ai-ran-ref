"""smo_shared.ratelimit — token buckets per caller (PR-SEC-8.2), on a fake clock.

Run with: cd smo/shared && PYTHONPATH=. python -m pytest tests/test_ratelimit.py -q
"""

import threading

from smo_shared import ratelimit
from smo_shared.ratelimit import TokenBuckets


class Clock:
    """A settable fake clock: calling it returns `now`."""
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def buckets(rate=2.0, burst=4.0):
    """Helper: token buckets with a constant rate and burst on a fake clock; returns the buckets and the clock."""
    clock = Clock()
    return TokenBuckets(lambda: rate, lambda: burst, clock), clock


def test_a_caller_may_burst_then_is_held_to_the_rate():
    """A caller can take `burst` requests at once, is then refused with a wait of one second (rounded up), and gets tokens back at `rate` per second
    without exceeding the burst.
    """
    b, clock = buckets(rate=2, burst=4)
    assert [b.take("a") for _ in range(4)] == [None] * 4
    assert b.take("a") == 1                                    # empty: half a second to the next token, rounded up
    clock.now += 0.5
    assert b.take("a") is None and b.take("a") == 1
    clock.now += 10                                            # idle: the bucket is refilled, not past its size
    assert [b.take("a") for _ in range(4)] == [None] * 4 and b.take("a") is not None


def test_retry_after_is_the_whole_seconds_until_one_token_is_back():
    """The wait reported to a refused caller is the whole seconds until one token is back, shrinking as time passes."""
    b, clock = buckets(rate=0.25, burst=1)
    assert b.take("a") is None
    assert b.take("a") == 4
    clock.now += 3
    assert b.take("a") == 1
    clock.now += 1
    assert b.take("a") is None


def test_callers_do_not_share_a_bucket():
    """One caller emptying its bucket does not affect another caller."""
    b, _ = buckets(rate=1, burst=1)
    assert b.take("noisy") is None and b.take("noisy") is not None
    assert b.take("quiet") is None


def test_a_rate_of_zero_or_less_turns_it_off_and_tracks_nobody():
    """A rate of 0 or less switches the limiter off: every request passes and no caller is remembered."""
    b, _ = buckets(rate=0, burst=1)
    assert all(b.take("a") is None for _ in range(50)) and len(b) == 0


def test_the_settings_are_read_on_every_call():
    """Rate and burst are read on each call, so the limiter can be switched off at run time."""
    state = {"rate": 1.0, "burst": 1.0}
    clock = Clock()
    b = TokenBuckets(lambda: state["rate"], lambda: state["burst"], clock)
    assert b.take("a") is None and b.take("a") is not None
    state["rate"] = 0
    assert b.take("a") is None                                 # switched off at run time


def test_idle_callers_are_forgotten_once_their_bucket_would_be_full(monkeypatch):
    """When the table passes its size bound, callers whose buckets would be full again are forgotten."""
    monkeypatch.setattr(ratelimit, "MAX_TRACKED_CALLERS", 5)
    b, clock = buckets(rate=1, burst=2)
    for n in range(5):
        b.take(f"c{n}")
    clock.now += 100                                           # all of them idle for a long time
    b.take("fresh")
    b.take("fresh2")                                           # the table is over its bound: idle buckets go
    assert len(b) <= 2


def test_concurrent_callers_never_get_more_than_the_burst():
    """Eight threads together get exactly the burst (50) and no more, so the bucket is safe under concurrency."""
    b, _ = buckets(rate=0.000001, burst=50)
    allowed = []
    lock = threading.Lock()

    def worker():
        for _ in range(20):
            if b.take("a") is None:
                with lock:
                    allowed.append(1)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    assert len(allowed) == 50


def test_idle_callers_are_forgotten_once_the_table_is_over_its_limit(monkeypatch):
    # found by the mutation pilot (V-2c): nothing tested that the table of buckets stops growing. A bucket is idle when it would be full again.
    """The table is trimmed only when it goes over the limit, and only buckets that are full again are removed (a partly refilled one stays)."""
    monkeypatch.setattr(ratelimit, "MAX_TRACKED_CALLERS", 3)
    limiter, clock = buckets(rate=0.5, burst=2.0)
    for caller, at in (("x", 1000.0), ("y", 1008.0), ("z", 1009.0)):
        clock.now = at
        assert limiter.take(caller) is None
    assert len(limiter) == 3                      # at the limit, not over it: nobody is forgotten yet
    clock.now = 1010.0
    assert limiter.take("w") is None              # the fourth caller takes the table over the limit
    # x refilled long ago and y exactly to its burst (1 + 2 s x 0.5 = 2): both forgotten. z (1.5 of 2) and w are still in use.
    assert set(limiter._buckets) == {"z", "w"}


def test_a_forgotten_caller_starts_again_with_a_full_burst(monkeypatch):
    """A caller whose bucket was forgotten starts again with a full burst."""
    monkeypatch.setattr(ratelimit, "MAX_TRACKED_CALLERS", 1)
    limiter, clock = buckets(rate=1.0, burst=2.0)
    assert limiter.take("a") is None
    clock.now += 100
    assert limiter.take("b") is None              # a is idle and forgotten
    assert "a" not in limiter._buckets
    assert limiter.take("a") is None and limiter.take("a") is None   # a full burst of 2 again
