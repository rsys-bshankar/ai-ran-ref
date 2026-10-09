"""PR-OBS-4: the `smo_intents{admin_state}` gauge follows the `intent` table (intents are the policy objects of this build; A1 policies left it).

Uses `smo_shared.testing.make_test_engine` (SQLite) and the module's own models; importing `app.main` is what registers the gauge. Run:
`cd smo/intent-service && PYTHONPATH=.:../shared python -m pytest tests/test_business_metrics.py -q`. Needs nothing external.
"""

import uuid

from sqlalchemy.orm import sessionmaker

from smo_shared.db import Base
from smo_shared.metrics import _query_gauges
from smo_shared.testing import make_test_engine

from app import main as _main  # noqa: F401  (importing the app registers the gauge)
from app import models as intent_models
from app.models import Intent, IntentHandlingFunction


def test_intents_are_counted_by_admin_state_with_both_states_present():
    """With no intents the gauge reports ACTIVATED and DEACTIVATED both at 0 (so a dashboard never sees a missing series); after inserting 2 and 1 it reports 2 and 1.
    The cache is cleared by hand because the gauge caches its reading for 15 seconds.
    """
    engine = make_test_engine()
    Base.metadata.create_all(engine, tables=[
        cls.__table__ for cls in vars(intent_models).values()
        if isinstance(cls, type) and issubclass(cls, Base) and cls is not Base and cls.__module__ == intent_models.__name__])
    factory = sessionmaker(bind=engine)
    gauge = _query_gauges["smo_intents"]
    gauge._session_factory, gauge._ttl, gauge._cached = factory, 0, None
    assert {s.labels["admin_state"]: s.value for family in gauge.collect() for s in family.samples} == {"ACTIVATED": 0, "DEACTIVATED": 0}
    columns = {c.name for c in IntentHandlingFunction.__table__.columns if not c.nullable and c.default is None and c.server_default is None}
    with factory() as s:
        s.add(IntentHandlingFunction(**{name: "x" for name in columns}))
        s.commit()
        for state in ("ACTIVATED", "ACTIVATED", "DEACTIVATED"):
            s.add(Intent(intent_id=uuid.uuid4(), intent_expectations=[], rmio_id="r", rmih_id="x", intent_admin_state=state))
        s.commit()
    gauge._cached = None
    assert {s.labels["admin_state"]: s.value for family in gauge.collect() for s in family.samples} == {"ACTIVATED": 2, "DEACTIVATED": 1}
