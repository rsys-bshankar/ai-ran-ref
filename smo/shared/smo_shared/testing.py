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
"""

import json
import uuid

from sqlalchemy import create_engine
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
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        json_serializer=_uuid_aware_json_serializer,
    )
