import os
import sys
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# All fourteen modules share ONE Postgres instance, partitioned by moduleScope
# per Requirements v0.1 section 3 — not one DB per module. Each module's models set
# module_scope on write; queries are expected to filter by it explicitly rather
# than relying on schema-level isolation, matching the pattern already used for
# SME/DME/Onboarding/rApp Mgmt in the HLD.
#
# There is no default URL (PR-DB-1): a default would put a known database password in every image and
# let a service that lost its configuration quietly talk to whatever answers on `postgres:5432`.
# `SMO_DATABASE_URL` must be set, or the process refuses to start with a message saying so.
class MissingDatabaseUrl(RuntimeError):
    """`SMO_DATABASE_URL` is not set."""


# The unit tests build the engine at import without a database (they override the session, or use
# `testing.make_test_engine()`), so under pytest an unset URL is an in-memory SQLite, never a server.
TEST_DATABASE_URL = "sqlite://"


def resolve_database_url(environ=os.environ, under_pytest: bool | None = None) -> str:
    url = environ.get("SMO_DATABASE_URL", "").strip()
    if url:
        return url
    if under_pytest is None:
        under_pytest = "pytest" in sys.modules
    if under_pytest:
        return TEST_DATABASE_URL
    raise MissingDatabaseUrl(
        "SMO_DATABASE_URL is not set. Set it to the Postgres URL for this deployment, for example "
        "postgresql+psycopg://<user>:<password>@<host>:5432/<database> (docker compose reads it from "
        "smo/.env, see .env.example). There is deliberately no default."
    )


DATABASE_URL = resolve_database_url()



def engine_options(url: str, environ=os.environ) -> dict:
    """`create_engine` keyword arguments for `url`, tuned from the environment (PR-ST-6).

    Connection pool (one pool per process, so a module run as N replicas with W workers each holds up to
    N x W x (pool_size + max_overflow) connections; size it against Postgres's `max_connections`):

      SMO_DB_POOL_SIZE                5      connections kept open per process
      SMO_DB_MAX_OVERFLOW             10     extra connections allowed under load
      SMO_DB_POOL_TIMEOUT_SECONDS     30     how long a request waits for a free connection
      SMO_DB_POOL_RECYCLE_SECONDS     1800   replace a connection older than this (0: never)

    Postgres session limits, so a stuck query or a leaked transaction cannot hold a connection for ever
    (0 turns a limit off):

      SMO_DB_STATEMENT_TIMEOUT_MS                    30000    cancel a statement that runs longer
      SMO_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS          300000   end a session idle inside a transaction this long

    The idle-in-transaction default is deliberately longer than any request: a route may hold a transaction
    open across calls to other modules (each bounded by `timeouts.py`).

    SQLite (the unit tests) gets none of these: it has no server-side pool or session limits.
    """
    options: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        return options

    def number(name: str, default: int) -> int:
        return int(environ.get(name, default))

    options["pool_size"] = number("SMO_DB_POOL_SIZE", 5)
    options["max_overflow"] = number("SMO_DB_MAX_OVERFLOW", 10)
    options["pool_timeout"] = number("SMO_DB_POOL_TIMEOUT_SECONDS", 30)
    recycle = number("SMO_DB_POOL_RECYCLE_SECONDS", 1800)
    options["pool_recycle"] = recycle if recycle > 0 else -1
    if url.startswith("postgresql"):
        session_limits = []
        statement = number("SMO_DB_STATEMENT_TIMEOUT_MS", 30000)
        idle = number("SMO_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS", 300000)
        if statement > 0:
            session_limits.append(f"-c statement_timeout={statement}")
        if idle > 0:
            session_limits.append(f"-c idle_in_transaction_session_timeout={idle}")
        if session_limits:
            options["connect_args"] = {"options": " ".join(session_limits)}
    return options


def build_engine(url: str, environ=os.environ):
    return create_engine(url, **engine_options(url, environ))


engine = build_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


@contextmanager
def session_scope() -> Session:
    """Provide a transactional scope for a single unit of work."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session():
    """FastAPI dependency — yields a session, always closes it."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
