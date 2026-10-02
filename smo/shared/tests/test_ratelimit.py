"""smo_shared.ratelimit — token buckets per caller (PR-SEC-8.2), on a fake clock."""

import threading

from smo_shared import ratelimit
from smo_shared.ratelimit import TokenBuckets


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def buckets(rate=2.0, burst=4.0):
    clock = Clock()
    return TokenBuckets(lambda: rate, lambda: burst, clock), clock


def test_a_caller_may_burst_then_is_held_to_the_rate():
    b, clock = buckets(rate=2, burst=4)
    assert [b.take("a") for _ in range(4)] == [None] * 4
    assert b.take("a") == 1                                    # empty: half a second to the next token, rounded up
    clock.now += 0.5
    assert b.take("a") is None and b.take("a") == 1
    clock.now += 10                                            # idle: the bucket is refilled, not past its size
    assert [b.take("a") for _ in range(4)] == [None] * 4 and b.take("a") is not None


def test_retry_after_is_the_whole_seconds_until_one_token_is_back():
    b, clock = buckets(rate=0.25, burst=1)
    assert b.take("a") is None
    assert b.take("a") == 4
    clock.now += 3
    assert b.take("a") == 1
    clock.now += 1
    assert b.take("a") is None


def test_callers_do_not_share_a_bucket():
    b, _ = buckets(rate=1, burst=1)
    assert b.take("noisy") is None and b.take("noisy") is not None
    assert b.take("quiet") is None


def test_a_rate_of_zero_or_less_turns_it_off_and_tracks_nobody():
    b, _ = buckets(rate=0, burst=1)
    assert all(b.take("a") is None for _ in range(50)) and len(b) == 0


def test_the_settings_are_read_on_every_call():
    state = {"rate": 1.0, "burst": 1.0}
    clock = Clock()
    b = TokenBuckets(lambda: state["rate"], lambda: state["burst"], clock)
    assert b.take("a") is None and b.take("a") is not None
    state["rate"] = 0
    assert b.take("a") is None                                 # switched off at run time


def test_idle_callers_are_forgotten_once_their_bucket_would_be_full(monkeypatch):
    monkeypatch.setattr(ratelimit, "MAX_TRACKED_CALLERS", 5)
    b, clock = buckets(rate=1, burst=2)
    for n in range(5):
        b.take(f"c{n}")
    clock.now += 100                                           # all of them idle for a long time
    b.take("fresh")
    b.take("fresh2")                                           # the table is over its bound: idle buckets go
    assert len(b) <= 2


def test_concurrent_callers_never_get_more_than_the_burst():
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
