"""Shared test-engine helper.

Two portability gaps show up repeatedly when running this SMO's real
Postgres-shaped models against an in-memory SQLite test DB:

  1. Plain "sqlite://" opens a NEW blank in-memory DB per pooled
     connection, so a FastAPI per-request session can grab a different
     one than whatever ran create_all() ("no such table"). Fixed with
     StaticPool — one shared connection for the life of the engine.
  2. ARRAY(Uuid) columns' sqlite JSON-variant fallback (see each
     models.py's `.with_variant(JSON(...), "sqlite")`) needs a JSON
     encoder that knows how to serialize a raw uuid.UUID object — the
     stdlib json module doesn't, by default, and Postgres's native
     ARRAY(Uuid) never needs this at all (psycopg adapts UUID objects
     directly), so this is purely a test-engine concern, never a
     production one.
  3. pysqlite's own legacy transaction handling silently auto-commits
     and auto-begins around DML in ways SQLAlchemy's own Session
     bookkeeping doesn't expect — invisible with one Session, but a real
     issue for this build's cross-service integration tests
     (tests_integration/), which route real nested FastAPI requests
     (so-smos -> nfo -> focom) through separate Session objects that all
     share this StaticPool's one physical connection. Caught as a real,
     reproducible `StaleDataError` on a second real cross-service order
     dispatch in the same test — never a genuine app bug (confirmed by
     the identical sequence succeeding against a real local Postgres
     instance, where every Session gets its own real connection): a
     nested Session's own commit was silently committing the outer
     Session's not-yet-committed work too, on the one shared connection,
     leaving the outer Session's bookkeeping unaware its later UPDATE
     needed a fresh transaction. Fixed with SQLAlchemy's own documented
     pysqlite workaround (disable pysqlite's implicit transaction
     handling; let SQLAlchemy issue BEGIN/COMMIT explicitly), applying
     project-wide since any future multi-hop nested-session integration
     test would hit the exact same class of failure.
"""

import json
import uuid

from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool


def _uuid_aware_default(obj):
    if isinstance(obj, uuid.UUID):
        return str(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def _uuid_aware_json_serializer(*args, **kwargs):
    return json.dumps(*args, default=_uuid_aware_default, **kwargs)


def make_test_engine():
    """A single-shared-connection in-memory SQLite engine, safe for
    FastAPI TestClient use, that can also round-trip ARRAY(Uuid) columns'
    sqlite JSON fallback.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        json_serializer=_uuid_aware_json_serializer,
    )

    @event.listens_for(engine, "connect")
    def _do_connect(dbapi_connection, connection_record):
        # Disable pysqlite's own implicit BEGIN/COMMIT entirely — see the
        # module docstring's point 3.
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _do_begin(conn):
        conn.exec_driver_sql("BEGIN")

    return engine
