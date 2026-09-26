"""Shared fixtures for cross-service integration tests.

Loads all fourteen modules (plus the mock Near-RT RIC) into one process
via loader.py, wires every one to ONE shared test-DB engine — matching
the real deployment's one-shared-Postgres-instance topology (Requirements
v0.1 section 3) — and installs the in-process service mesh so R1Client
and A1TerminationClient calls land on the right module instead of going
out over a real network.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))

from smo_shared.db import Base, get_session  # noqa: E402
from smo_shared.testing import make_test_engine  # noqa: E402

from loader import load_app_module  # noqa: E402
from mesh import ServiceMesh, install as install_mesh  # noqa: E402

ALL_MODULES = [
    "r1-termination", "sme", "dme", "onboarding", "rapp-mgmt", "ran-nf-oam",
    "a1-related", "nfo", "focom", "ai-ml-workflow", "ran-analytics",
    "intent-service", "so-smos", "sa-smos", "mock-near-rt-ric", "mock-o1-adaptor",
]


@pytest.fixture(scope="session")
def loaded_apps():
    """Loads every module's app.main exactly once for the whole test
    session — loading twice would try to redefine the same SQLAlchemy
    mapped classes against the same Base.metadata a second time.
    """
    return {name: load_app_module(name) for name in ALL_MODULES}


@pytest.fixture(scope="session")
def shared_engine(loaded_apps):
    """Loading every module (above) already registered every module's
    tables onto Base.metadata as a side effect of importing their
    models.py — so by the time this fixture runs, create_all() builds
    the FULL consolidated schema in one shot.
    """
    engine = make_test_engine()
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_connection(shared_engine):
    """One shared Connection, inside one open outer transaction this
    fixture rolls back at teardown — not a bare `shared_engine`. A
    cross-service call chain three deep (so-smos -> nfo -> focom, each a
    real nested FastAPI request) means three separate Session objects
    live at once, all funneled onto this engine's one StaticPool
    connection (smo_shared/testing.py). Each grabbing its OWN
    transaction on that one physical connection is a real bug, caught by
    a genuine `StaleDataError` on a second real cross-service order
    dispatch: an inner Session's commit was silently committing the
    outer Session's not-yet-committed work too (invisible with
    pysqlite's legacy auto-transaction behavior, until testing.py's own
    pysqlite fix turned it into an outright `OperationalError`, "cannot
    start a transaction within a transaction" — confirming this, not a
    proper fix on its own). Every Session in this test — whether `mesh`'s
    own per-request ones or a test's own direct `Session(bind=
    db_connection, join_transaction_mode="create_savepoint")` peek —
    must bind to THIS connection so they all share the one real
    transaction via SAVEPOINT nesting (SQLAlchemy's own documented fix
    for this shape of fixture): a nested Session's own commit then only
    releases its savepoint, never an outer Session's still-open work.
    Confirmed this was purely a test-harness artifact, not a real app
    bug: the identical sequence already passes against a real local
    Postgres instance, where every Session gets its own real connection.
    """
    connection = shared_engine.connect()
    outer_transaction = connection.begin()
    yield connection
    # clean slate for the next test — nothing this test did was ever
    # really committed (it all happened inside savepoints under this
    # one still-open outer transaction), so rolling it back is both
    # correct and simpler than a per-table DELETE sweep.
    outer_transaction.rollback()
    connection.close()


@pytest.fixture
def mesh(loaded_apps, db_connection, monkeypatch):
    """Function-scoped: fresh DB rows and a fresh mesh per test, but the
    same loaded app objects and engine across the whole session — the
    apps themselves are stateless (all state lives in the DB), so
    reloading them per test would just be wasted work. See
    `db_connection`'s own docstring for why every per-request Session
    binds to that one shared connection rather than `shared_engine`
    directly.

    `expire_on_commit=False` closes a second, subtler bug the savepoint
    fix alone didn't: SQLAlchemy's default `expire_on_commit=True` means
    any attribute read on an outer Session's object AFTER its own
    commit() (e.g. rapp-mgmt's `terminate_instance` building a URL from
    `inst.package_id` right after committing `inst.state`) silently
    starts a SECOND implicit transaction on that Session — a new,
    unreleased savepoint. If a nested cross-service call then commits
    (releasing ITS OWN savepoint into that still-open second one) before
    the outer Session is closed, `Session.close()`'s own implicit
    rollback of that never-explicitly-committed second savepoint takes
    the nested commit down with it — silently, no exception, the nested
    write just never happened as far as any later Session can see. Caught
    the same way as the fix above: confirmed purely a test-harness
    artifact (the identical onboarding-prime/deprime/terminate sequence
    passes against a real local Postgres instance) before fixing it here.
    """
    TestSession = sessionmaker(bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    clients: dict[str, TestClient] = {}
    for name, main_module in loaded_apps.items():
        main_module.app.dependency_overrides[get_session] = override_get_session
        clients[name] = TestClient(main_module.app, raise_server_exceptions=False)

    m = ServiceMesh(clients)
    install_mesh(monkeypatch, m)

    yield clients

    for main_module in loaded_apps.values():
        main_module.app.dependency_overrides.clear()
