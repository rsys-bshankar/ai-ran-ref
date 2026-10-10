"""Shared fixture of the R1 Termination tests: a database for the tables the gateway touches.

The gateway reads the kill switch (`rapp_kill`) and writes the audit chain in the shared database, so every test gets an in-memory SQLite one with those
tables and a cleared kill-switch cache, unless the test installs its own. Used automatically (autouse) by every file in this directory; run the suite with
`cd smo/r1-termination && PYTHONPATH=.:../shared python -m pytest tests -q`.
"""

import pytest
from sqlalchemy.orm import sessionmaker

from smo_shared import audit, killswitch  # noqa: F401  (audit registers its tables on the shared Base)
from smo_shared.db import Base
from smo_shared.testing import make_test_engine


@pytest.fixture(autouse=True)
def gateway_database(monkeypatch):
    """Autouse fixture: an in-memory engine with the shared tables and `rapp_kill`, installed as `smo_shared.db.SessionLocal`; yields the engine.

    Tests may assume `R1_KILL_SWITCH` is unset, the kill-switch cache is empty before and after, and nothing is stopped or audited yet.
    """
    engine = make_test_engine()
    Base.metadata.create_all(engine)
    killswitch.METADATA.create_all(engine)
    monkeypatch.setattr("smo_shared.db.SessionLocal", sessionmaker(bind=engine, autoflush=False, future=True))
    monkeypatch.delenv("R1_KILL_SWITCH", raising=False)
    killswitch.clear_cache()
    yield engine
    killswitch.clear_cache()
