"""PR-SEC-8.5: the shared limiter's one-statement token bucket, on a real Postgres (LEAST/GREATEST, row locking and the migrated `rate_bucket` table).

Needs SMO_TEST_POSTGRES_URL (CI's `migration-postgres` job, or a local server); skipped without it. The same arithmetic runs on SQLite in
shared/tests/test_ratelimit_shared.py; what only Postgres can say is that the statement is valid there and that replicas, each with its own
connections, never lose an update on one caller's row.
"""

import threading

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from smo_shared.ratelimit import SharedTokenBuckets
from test_db_roles import database, needs_postgres  # noqa: F401  (the `database` fixture: a migrated database)


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def _replica(url, rate, burst, clock):
    engine = create_engine(url, pool_size=8)                                  # its own pool, as a gateway replica has
    return SharedTokenBuckets(lambda: rate, lambda: burst, clock, sessionmaker(bind=engine, future=True)), engine


@needs_postgres
def test_the_statement_is_a_token_bucket_on_postgres(database):
    """On Postgres the one-statement bucket allows the burst, refuses without taking tokens, refills with time up to the burst, keeps a separate
    bucket per caller and records whether the last call was allowed.
    """
    clock = Clock()
    limiter, engine = _replica(database[0], rate=2.0, burst=4.0, clock=clock)
    assert [limiter.take("a") for _ in range(4)] == [None] * 4
    assert limiter.take("a") == 1 and limiter.take("a") == 1                 # empty, and a refused request takes nothing
    clock.now += 0.5
    assert limiter.take("a") is None and limiter.take("a") == 1
    clock.now += 100
    assert [limiter.take("a") for _ in range(4)] == [None] * 4 and limiter.take("a") is not None
    assert limiter.take("b") is None                                          # another caller
    with engine.connect() as connection:
        row = connection.execute(text("SELECT tokens, last_allowed FROM rate_bucket WHERE caller = 'a'")).one()
    assert row.tokens < 1 and row.last_allowed is False
    engine.dispose()


@needs_postgres
def test_replicas_with_their_own_connections_share_one_budget_under_concurrency(database):
    """Three replicas with their own connections, under concurrent threads, share one budget (120 attempts against a burst of 40) and lose no
    update on the caller's row.
    """
    clock = Clock()
    replicas = [_replica(database[0], rate=0.000001, burst=40.0, clock=clock) for _ in range(3)]
    allowed, lock = [], threading.Lock()

    def worker(limiter):
        for _ in range(20):
            if limiter.take("caller") is None:
                with lock:
                    allowed.append(1)
    threads = [threading.Thread(target=worker, args=(replicas[n % 3][0],)) for n in range(6)]       # 6 x 20 = 120 attempts against a burst of 40
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    assert len(allowed) == 40                                                  # one budget for all three replicas, exactly, no lost update
    [engine.dispose() for _, engine in replicas]


@needs_postgres
def test_purge_removes_the_buckets_that_are_full_again_and_not_the_others(database):
    """`purge` removes the buckets that have refilled to full and keeps the others."""
    clock = Clock()
    limiter, engine = _replica(database[0], rate=1.0, burst=2.0, clock=clock)
    limiter.take("idle")
    limiter.take("busy")
    limiter.take("busy")
    clock.now += 1.0                                                           # idle: 1 + 1 >= 2 (full); busy: 0 + 1 < 2
    assert limiter.purge() == 1 and len(limiter) == 1
    engine.dispose()


@needs_postgres
def test_a_statement_stamped_earlier_than_the_rows_clock_does_not_refill_the_same_time_twice(database):
    """Statements issued together are applied in any order (a replica stamps one before it waits for the caller's row): one stamped earlier must leave the bucket's clock where it was (GREATEST)."""
    clock = Clock()
    limiter, engine = _replica(database[0], rate=1.0, burst=3.0, clock=clock)
    assert [limiter.take("a") for _ in range(3)] == [None] * 3 and limiter.take("a") is not None      # empty at the start
    start = clock.now
    clock.now = start - 1.0                                                                            # issued earlier, applied later
    assert limiter.take("a") is not None
    clock.now = start
    assert limiter.take("a") is not None                                                               # no token for a second that was counted already
    clock.now = start + 1.0
    assert limiter.take("a") is None and limiter.take("a") is not None
    with engine.connect() as db:
        assert db.execute(text("SELECT refilled_at FROM rate_bucket WHERE caller = 'a'")).scalar_one() == start + 1.0
