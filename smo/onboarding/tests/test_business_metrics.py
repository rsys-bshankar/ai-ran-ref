"""PR-OBS-4: `smo_rapp_packages{state}` follows the application_package table."""

from sqlalchemy import Column, Table, Uuid, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from smo_shared.db import Base
from smo_shared.metrics import _query_gauges

from app import main as _main  # noqa: F401  (importing the app registers the gauge)
from app.models import ApplicationPackage, Artifact, PackageUsageRegistration
from app.statemachine import PackageState


def test_packages_are_counted_by_state_with_every_state_present():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    if "nf_deployment_descriptor" not in Base.metadata.tables:
        Table("nf_deployment_descriptor", Base.metadata, Column("nf_deployment_descriptor_id", Uuid, primary_key=True))
    Base.metadata.create_all(engine, tables=[ApplicationPackage.__table__, Artifact.__table__, PackageUsageRegistration.__table__])
    factory = sessionmaker(bind=engine)
    with factory() as s:
        for state in ("AVAILABLE", "AVAILABLE", "FAILED"):
            s.add(ApplicationPackage(application_type="rApp", name="p", version="1.0", manifest_ref="m", state=state))
        s.commit()
    gauge = _query_gauges["smo_rapp_packages"]
    gauge._session_factory, gauge._ttl, gauge._cached = factory, 0, None
    samples = {s.labels["state"]: s.value for family in gauge.collect() for s in family.samples}
    assert samples["AVAILABLE"] == 2 and samples["FAILED"] == 1
    assert set(samples) == {state.value for state in PackageState}
