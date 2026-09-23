"""Tests for the ApplicationPackage lifecycle (Onboarding/rApp Mgmt LLD
sections 3-4). Run with: pytest smo/onboarding/tests -q
"""

import uuid

import pytest
from sqlalchemy import Column, Table, Uuid, create_engine
from sqlalchemy.orm import Session

from smo_shared.db import Base
from smo_shared.statemachine import IllegalTransition

from app.models import ApplicationPackage, PackageUsageRegistration
from app.statemachine import ONBOARDING_FSM, PackageEvent, PackageState


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    # nf_deployment_descriptor lives in the nfo module, out of scope for this
    # test package — stand in a minimal table so ApplicationPackage's FK
    # resolves. Production runs against the full consolidated migration
    # (001_init.sql), same pattern as nfo/tests' application_package stub.
    if "nf_deployment_descriptor" not in Base.metadata.tables:
        Table("nf_deployment_descriptor", Base.metadata, Column("nf_deployment_descriptor_id", Uuid, primary_key=True))
    Base.metadata.create_all(engine, tables=[ApplicationPackage.__table__, PackageUsageRegistration.__table__])
    with Session(engine) as session:
        yield session


def make_package(db, state=PackageState.ONBOARDING, parent_id=None) -> ApplicationPackage:
    pkg = ApplicationPackage(
        application_type="rApp", name="hello-world", version="1.0",
        state=state, manifest_ref="s3://pkg.csar", parent_package_id=parent_id,
    )
    db.add(pkg)
    db.flush()
    return pkg


def test_onboard_success_reaches_available(db):
    pkg = make_package(db)
    new_state = ONBOARDING_FSM.fire(PackageState(pkg.state), PackageEvent.VALIDATE_OK, db=db, package=pkg)
    assert new_state == PackageState.AVAILABLE


def test_onboard_failure_reaches_failed_not_available(db):
    pkg = make_package(db)
    new_state = ONBOARDING_FSM.fire(PackageState(pkg.state), PackageEvent.VALIDATE_FAILED, db=db, package=pkg)
    assert new_state == PackageState.FAILED


def test_deprecate_then_cancel_delete_returns_to_available(db):
    pkg = make_package(db, state=PackageState.AVAILABLE)
    s = ONBOARDING_FSM.fire(PackageState.AVAILABLE, PackageEvent.DEPRECATE, db=db, package=pkg)
    assert s == PackageState.DEPRECATED
    s = ONBOARDING_FSM.fire(s, PackageEvent.CANCEL_DELETE, db=db, package=pkg)
    assert s == PackageState.AVAILABLE


def test_delete_blocked_by_available_child(db):
    """The cascade-delete rule this LLD pass concretized: a parent cannot
    be deleted while a child package is AVAILABLE.
    """
    parent = make_package(db, state=PackageState.DEPRECATED)
    make_package(db, state=PackageState.AVAILABLE, parent_id=parent.package_id)

    with pytest.raises(IllegalTransition):
        ONBOARDING_FSM.fire(PackageState.DEPRECATED, PackageEvent.DELETE, db=db, package=parent)


def test_delete_blocked_by_active_usage_registration(db):
    pkg = make_package(db, state=PackageState.DEPRECATED)
    db.add(PackageUsageRegistration(package_id=pkg.package_id, consumer_id="some-rapp", stopped_at=None))
    db.flush()

    with pytest.raises(IllegalTransition):
        ONBOARDING_FSM.fire(PackageState.DEPRECATED, PackageEvent.DELETE, db=db, package=pkg)


def test_delete_allowed_once_usage_stopped(db):
    import datetime

    pkg = make_package(db, state=PackageState.DEPRECATED)
    db.add(PackageUsageRegistration(
        package_id=pkg.package_id, consumer_id="some-rapp",
        stopped_at=datetime.datetime.now(datetime.UTC),
    ))
    db.flush()

    new_state = ONBOARDING_FSM.fire(PackageState.DEPRECATED, PackageEvent.DELETE, db=db, package=pkg)
    assert new_state == PackageState.DELETING


def test_no_transition_from_failed(db):
    """FAILED is terminal within this FSM — nothing in v1.3 or the LLD
    proposes a recovery path out of it.
    """
    pkg = make_package(db, state=PackageState.FAILED)
    with pytest.raises(IllegalTransition):
        ONBOARDING_FSM.fire(PackageState.FAILED, PackageEvent.VALIDATE_OK, db=db, package=pkg)


def test_prime_then_deprime_round_trip(db):
    """OPEN_ITEMS.md section 5: the reference's real
    COMMISSIONED->PRIMING->PRIMED->DEPRIMING lifecycle, missing
    entirely before this pass.
    """
    pkg = make_package(db, state=PackageState.AVAILABLE)
    s = ONBOARDING_FSM.fire(PackageState.AVAILABLE, PackageEvent.PRIME, db=db, package=pkg)
    assert s == PackageState.PRIMING
    s = ONBOARDING_FSM.fire(s, PackageEvent.PRIME_COMPLETE, db=db, package=pkg)
    assert s == PackageState.PRIMED

    s = ONBOARDING_FSM.fire(s, PackageEvent.DEPRIME, db=db, package=pkg)
    assert s == PackageState.DEPRIMING
    s = ONBOARDING_FSM.fire(s, PackageEvent.DEPRIME_COMPLETE, db=db, package=pkg)
    assert s == PackageState.AVAILABLE


def test_prime_from_onboarding_is_illegal(db):
    """No PRIME edge exists from ONBOARDING (or DEPRECATED, DELETING,
    FAILED) — only AVAILABLE. A package that never reached AVAILABLE
    can't be primed.
    """
    pkg = make_package(db, state=PackageState.ONBOARDING)
    with pytest.raises(IllegalTransition):
        ONBOARDING_FSM.fire(PackageState.ONBOARDING, PackageEvent.PRIME, db=db, package=pkg)


def test_deprime_blocked_by_active_usage_registration(db):
    """The reference's own deprimeRapp guard: 'Unable to deprime as there
    are active rapp instances.'
    """
    pkg = make_package(db, state=PackageState.PRIMED)
    db.add(PackageUsageRegistration(package_id=pkg.package_id, consumer_id="some-rapp", stopped_at=None))
    db.flush()

    with pytest.raises(IllegalTransition):
        ONBOARDING_FSM.fire(PackageState.PRIMED, PackageEvent.DEPRIME, db=db, package=pkg)


def test_delete_has_no_edge_from_primed(db):
    """Matches the reference's own deleteRapp guard: delete is only
    permitted from COMMISSIONED (our AVAILABLE) — a PRIMED package
    must be deprimed first, same as the reference's error
    'the rApp is not in COMMISSIONED state'.
    """
    pkg = make_package(db, state=PackageState.PRIMED)
    with pytest.raises(IllegalTransition):
        ONBOARDING_FSM.fire(PackageState.PRIMED, PackageEvent.DELETE, db=db, package=pkg)
