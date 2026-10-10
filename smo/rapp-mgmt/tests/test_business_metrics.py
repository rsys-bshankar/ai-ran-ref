"""PR-OBS-4: `smo_rapp_instances{state}` follows the rapp_instance table; RUNNING are the active rApps."""

import uuid

from sqlalchemy import Column, Table, Uuid as UuidType, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from smo_shared.db import Base
from smo_shared.metrics import _query_gauges

from app import main as _main  # noqa: F401  (importing the app registers the gauge)
from app.models import RAppFaultReport, RAppInstance, RAppInstanceVersion, RAppPerformanceReport
from app.statemachine import InstanceState


def test_instances_are_counted_by_state_with_every_state_present():
    """The `smo_rapp_instances` gauge counts instances by state (RUNNING are the active rApps) and reports every state, with zero for the ones that have no row.
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    if "application_package" not in Base.metadata.tables:
        Table("application_package", Base.metadata, Column("package_id", UuidType, primary_key=True))
    if "package_usage_registration" not in Base.metadata.tables:
        Table("package_usage_registration", Base.metadata, Column("id", UuidType, primary_key=True))
    Base.metadata.create_all(engine, tables=[
        Base.metadata.tables["application_package"], Base.metadata.tables["package_usage_registration"],
        RAppInstance.__table__, RAppFaultReport.__table__, RAppPerformanceReport.__table__, RAppInstanceVersion.__table__])
    factory = sessionmaker(bind=engine)
    with factory() as s:
        for state in ("RUNNING", "RUNNING", "RUNNING", "FAULTED"):
            s.add(RAppInstance(package_id=uuid.uuid4(), state=state))
        s.commit()
    gauge = _query_gauges["smo_rapp_instances"]
    gauge._session_factory, gauge._ttl, gauge._cached = factory, 0, None
    samples = {s.labels["state"]: s.value for family in gauge.collect() for s in family.samples}
    assert samples["RUNNING"] == 3 and samples["FAULTED"] == 1
    assert set(samples) == {state.value for state in InstanceState}
