"""The gateway reads the kill switch (`rapp_kill`) and writes the audit chain in the shared database: every test gets an in-memory one with those tables,
and a cleared kill-switch cache, unless it installs its own."""

import pytest
from sqlalchemy.orm import sessionmaker

from smo_shared import audit, killswitch  # noqa: F401  (audit registers its tables on the shared Base)
from smo_shared.db import Base
from smo_shared.testing import make_test_engine


@pytest.fixture(autouse=True)
def gateway_database(monkeypatch):
    engine = make_test_engine()
    Base.metadata.create_all(engine)
    killswitch.METADATA.create_all(engine)
    monkeypatch.setattr("smo_shared.db.SessionLocal", sessionmaker(bind=engine, autoflush=False, future=True))
    monkeypatch.delenv("R1_KILL_SWITCH", raising=False)
    killswitch.clear_cache()
    yield engine
    killswitch.clear_cache()
