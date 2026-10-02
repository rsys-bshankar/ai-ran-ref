"""Run a periodic task on one replica only (PR-ST-8).

Nothing in the platform needs a periodic tick today (`HISTORY.md` §10, ST-1.4), so this has no caller yet. It is
here so the first feature that does (a scheduled collection, a compare, an aging sweep) does not fire once per
replica, or start a scheduler inside a request process (`scripts/check_statelessness.py` refuses that).

    # called from whatever ticks: a Kubernetes CronJob, an external scheduler, or an endpoint hit on a timer
    run_once_per_interval("collect-pm", 900, collect_pm)

  run_once_per_interval(name, interval_seconds, fn)
        Every replica may call it as often as it likes; across all of them `fn` runs at most once per
        interval. The claim is one atomic `UPDATE ... WHERE last_run_at <= now - interval` on the shared
        `periodic_run` row, so it needs no leader, no clock agreement beyond the interval itself (replica
        clocks differing by seconds shift a firing by seconds), and survives restarts. It returns True when
        this call ran `fn`. If `fn` raises, the claim is given back and the exception propagates, so a failed
        run does not use up the interval and the next tick retries.
  advisory_lock(name)
        Postgres session advisory lock: yields True if this caller holds `name`, False if another does. Held
        for the block on a dedicated autocommit connection (a pooled, transaction-holding one would be ended
        by `idle_in_transaction_session_timeout`), and released by the server if the process dies, which is
        the lease: a crashed holder frees the lock without anyone cleaning up. `run_once_per_interval` holds it
        while `fn` runs, so a run longer than the interval is never started a second time on another replica.
        On a database that is not Postgres (the unit-test SQLite) it always yields True.
"""

import contextlib
import datetime
import hashlib
from collections.abc import Callable, Iterator

from sqlalchemy import DateTime, String, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

_NEVER = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


class PeriodicRun(Base):
    __tablename__ = "periodic_run"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    last_run_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _lock_key(name: str) -> int:
    """A stable signed 64-bit key for `pg_try_advisory_lock`, the same in every process."""
    return int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)


@contextlib.contextmanager
def advisory_lock(name: str, engine=None) -> Iterator[bool]:
    if engine is None:
        from .db import engine as default_engine  # resolved at call time, so importing this module opens nothing
        engine = default_engine
    if engine.dialect.name != "postgresql":
        yield True
        return
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        held = bool(connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": _lock_key(name)}).scalar())
        try:
            yield held
        finally:
            if held:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _lock_key(name)})


def _claim(session_factory, name: str, interval: datetime.timedelta, now: datetime.datetime) -> bool:
    with session_factory() as db:
        db.add(PeriodicRun(name=name, last_run_at=_NEVER))
        try:
            db.commit()                      # the first caller ever creates the row; later ones lose this race harmlessly
        except IntegrityError:
            db.rollback()
        claimed = db.execute(update(PeriodicRun).where(PeriodicRun.name == name, PeriodicRun.last_run_at <= now - interval)
                             .values(last_run_at=now))
        db.commit()
        return claimed.rowcount == 1


def _give_back(session_factory, name: str, claimed_at: datetime.datetime) -> None:
    with session_factory() as db:
        db.execute(update(PeriodicRun).where(PeriodicRun.name == name, PeriodicRun.last_run_at == claimed_at)
                   .values(last_run_at=_NEVER))
        db.commit()


def run_once_per_interval(name: str, interval_seconds: float, fn: Callable[[], None], *, session_factory=None,
                          engine=None, now: datetime.datetime | None = None) -> bool:
    """True if this call ran `fn`; False if another replica ran it within the interval (or is running it now)."""
    if session_factory is None:
        from .db import SessionLocal as session_factory  # noqa: N813 (a factory, resolved at call time)
    claimed_at = now or _now()
    if not _claim(session_factory, name, datetime.timedelta(seconds=interval_seconds), claimed_at):
        return False
    try:
        with advisory_lock(name, engine) as held:
            if not held:                     # an earlier run is still going on another replica
                _give_back(session_factory, name, claimed_at)
                return False
            fn()
    except BaseException:
        _give_back(session_factory, name, claimed_at)
        raise
    return True
