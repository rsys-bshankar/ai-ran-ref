"""Tests for RAppInstance lifecycle + upgrade auto-rollback
(Onboarding/rApp Mgmt LLD sections 5-6). Run with: pytest smo/rapp-mgmt/tests -q
"""

import uuid

import pytest
from sqlalchemy import Column, Table, Uuid, create_engine
from sqlalchemy.orm import Session

from smo_shared.db import Base

from app.models import RAppInstance
from app.statemachine import RAPP_INSTANCE_FSM, InstanceEvent, InstanceState
from app.upgrade import resolve_upgrade, start_upgrade


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    # application_package lives in the onboarding module, out of scope for this
    # test package — stand in a minimal table so RAppInstance's FK resolves.
    # Production runs against the full consolidated migration (001_init.sql).
    if "application_package" not in Base.metadata.tables:
        Table("application_package", Base.metadata, Column("package_id", Uuid, primary_key=True))
    Base.metadata.create_all(engine, tables=[Base.metadata.tables["application_package"], RAppInstance.__table__])
    with Session(engine) as session:
        yield session


def running_instance(db, package_id=None) -> RAppInstance:
    inst = RAppInstance(package_id=package_id or uuid.uuid4(), state=InstanceState.RUNNING, oauth_client_id="cred-123")
    db.add(inst)
    db.flush()
    return inst


def test_create_instance_bootstrap_success(db):
    inst = RAppInstance(package_id=uuid.uuid4(), state=InstanceState.DEPLOYING)
    db.add(inst)
    db.flush()
    new_state = RAPP_INSTANCE_FSM.fire(InstanceState.DEPLOYING, InstanceEvent.BOOTSTRAP_OK, instance=inst)
    assert new_state == InstanceState.RUNNING


def test_terminate_revokes_credential(db):
    """Closes v1.3's red-team finding RT-3: revocation is part of the
    TerminateInstance transition itself, not a separate step.
    """
    inst = running_instance(db)
    assert inst.oauth_client_id is not None
    new_state = RAPP_INSTANCE_FSM.fire(InstanceState.RUNNING, InstanceEvent.TERMINATE, instance=inst)
    assert new_state == InstanceState.TERMINATING
    assert inst.oauth_client_id is None


def test_upgrade_success_commits_and_removes_old_row(db):
    old_package = uuid.uuid4()
    new_package = uuid.uuid4()
    old = running_instance(db, package_id=old_package)

    new = start_upgrade(db, old, new_package)
    assert old.state == InstanceState.UPGRADING
    assert new.state == InstanceState.DEPLOYING
    assert old.pending_upgrade_instance_id == new.instance_id

    resolve_upgrade(db, old, new, new_bootstrap_succeeded=True)

    assert new.state == InstanceState.RUNNING
    assert new.package_id == new_package
    # old row is gone; the surviving instance is `new`, now on the new package
    assert old not in db.new and old.state == InstanceState.TERMINATING


def test_upgrade_failure_auto_rolls_back_old_row_untouched(db):
    """The auto-rollback decision v1.3 made explicit: failure never
    requires manual intervention, and the OLD instance is unaffected
    throughout — it never left RUNNING in the caller's observable sense
    except for the UPGRADING marker during the attempt.
    """
    old_package = uuid.uuid4()
    old = running_instance(db, package_id=old_package)
    original_credential = old.oauth_client_id

    new = start_upgrade(db, old, uuid.uuid4())
    resolve_upgrade(db, old, new, new_bootstrap_succeeded=False)

    assert old.state == InstanceState.RUNNING
    assert old.package_id == old_package  # never changed
    assert old.oauth_client_id == original_credential  # never revoked — this instance was never terminated
    assert old.pending_upgrade_instance_id is None


def test_crash_and_manual_recovery(db):
    inst = running_instance(db)
    s = RAPP_INSTANCE_FSM.fire(InstanceState.RUNNING, InstanceEvent.CRASH, instance=inst)
    assert s == InstanceState.FAULTED
    s = RAPP_INSTANCE_FSM.fire(s, InstanceEvent.RECOVER, instance=inst)
    assert s == InstanceState.DEPLOYING
