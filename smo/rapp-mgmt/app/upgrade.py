"""UpgradeInstance orchestration — Onboarding/rApp Mgmt LLD section 6.

Coordinates two RAppInstance rows through statemachine.py's per-row FSM.
Kept separate from the FSM itself because this is cross-entity choreography
(a saga), not a single state machine — same separation used for the
NFO -> FOCOM dependency in the NFO+FOCOM LLD.

The replacement row is a complete instance (OI-2-upgrade-completeness,
OI-1-upgrade-identity): it goes through CreateInstance's own
`provision_instance` (package-state check, its own fresh oauth_client_id,
NFO Instantiate, usage/start) and inherits the old row's configuration,
autonomy mode and region scope. Whichever row loses is torn down the way
TERMINATE tears an instance down — DME/SME deregistration and credential
revocation (the FSM action), then NFO Terminate and usage/stop
(`release_instance_resources`) — before its row is deleted.

upgradeTimeoutSeconds is enforced lazily: no scheduler exists in this
build, so an unresolved upgrade whose replacement was created more than
`old.upgrade_timeout_seconds` ago is rolled back the next time either row
is touched (`expire_overdue_upgrade`, called from main.py's reads and
lifecycle routes).
"""

import copy
import datetime
import uuid
from typing import Callable

from sqlalchemy.orm import Session

from smo_shared.statemachine import IllegalTransition
from smo_shared.timeutil import as_utc

from .models import RAppFaultReport, RAppInstance, RAppPerformanceReport
from .provisioning import provision_instance, release_instance_resources
from .statemachine import RAPP_INSTANCE_FSM, InstanceEvent, InstanceState, _terminate_side_effects


def start_upgrade(db: Session, old: RAppInstance, new_package_id: uuid.UUID) -> RAppInstance:
    """An instance that is not RUNNING is refused (IllegalTransition) before
    anything is provisioned; a package that is not AVAILABLE/PRIMED is
    refused by provision_instance before any NFO call — either way the old
    row is left untouched."""
    old_state = InstanceState(old.state)
    if InstanceEvent.START_UPGRADE not in RAPP_INSTANCE_FSM.legal_events(old_state):
        raise IllegalTransition(old_state, InstanceEvent.START_UPGRADE)
    new = provision_instance(db, new_package_id, configuration=copy.deepcopy(old.configuration),
                             autonomy_mode=old.autonomy_mode, region_scope=copy.deepcopy(old.region_scope))
    new.upgrade_timeout_seconds = old.upgrade_timeout_seconds
    old.state = RAPP_INSTANCE_FSM.fire(old_state, InstanceEvent.START_UPGRADE, instance=old)
    old.pending_upgrade_instance_id = new.instance_id
    return new


def upgrade_deadline(old: RAppInstance, new: RAppInstance) -> datetime.datetime:
    return as_utc(new.created_at) + datetime.timedelta(seconds=old.upgrade_timeout_seconds)


def _delete_row(db: Session, inst: RAppInstance) -> None:
    db.query(RAppFaultReport).filter(RAppFaultReport.instance_id == inst.instance_id).delete()
    db.query(RAppPerformanceReport).filter(RAppPerformanceReport.instance_id == inst.instance_id).delete()
    db.delete(inst)


def _roll_back(db: Session, old: RAppInstance, new: RAppInstance | None, reason: str) -> None:
    """Tears the replacement down like a TERMINATE (DME/SME deregistration
    if it had bootstrapped, credential revocation, NFO Terminate,
    usage/stop), deletes it, and returns the old row to RUNNING."""
    old_state = RAPP_INSTANCE_FSM.fire(InstanceState(old.state), InstanceEvent.UPGRADE_ROLLBACK, instance=old)
    if new is not None:
        _terminate_side_effects(new)
        old.last_teardown = release_instance_resources(new, reason)
        old.pending_upgrade_instance_id = None
        db.flush()  # clear the old row's FK to the replacement before deleting it
        _delete_row(db, new)
    old.state = old_state
    old.pending_upgrade_instance_id = None


def resolve_upgrade(db: Session, old: RAppInstance, new: RAppInstance, new_bootstrap_succeeded: bool,
                    register_identity: Callable[[RAppInstance], None] | None = None) -> None:
    """Called once the new instance's bootstrap outcome is known — either
    it reached RUNNING within old.upgrade_timeout_seconds, or it didn't
    (FAULTED or the timeout fired first). Auto-rollback, no manual
    intervention, per v1.3's own decision — this function is where that
    decision is actually executed.

    Commit: the replacement must be DEPLOYING (fires BOOTSTRAP_OK and runs
    `register_identity` — SME registration under its own identity) or
    already RUNNING (its container called bootstrap-complete itself); any
    other state raises IllegalTransition before anything is changed. The old
    row is then retired like a TERMINATE and deleted; its teardown outcome
    is recorded on the replacement's `last_teardown`.
    """
    if new_bootstrap_succeeded:
        new_state = InstanceState(new.state)
        if new_state not in (InstanceState.DEPLOYING, InstanceState.RUNNING):
            raise IllegalTransition(new_state, InstanceEvent.BOOTSTRAP_OK)
        if InstanceState(old.state) != InstanceState.UPGRADING:
            raise IllegalTransition(InstanceState(old.state), InstanceEvent.UPGRADE_COMMIT)
        if new_state == InstanceState.DEPLOYING:
            new.state = RAPP_INSTANCE_FSM.fire(new_state, InstanceEvent.BOOTSTRAP_OK, instance=new)
            if register_identity is not None:
                register_identity(new)
        old.state = RAPP_INSTANCE_FSM.fire(InstanceState(old.state), InstanceEvent.UPGRADE_COMMIT, instance=old)
        new.last_teardown = release_instance_resources(old, "UPGRADE_COMMIT")
        _delete_row(db, old)  # old row torn down once its replacement is confirmed RUNNING
    else:
        _roll_back(db, old, new, "UPGRADE_ROLLBACK")


def expire_overdue_upgrade(db: Session, old: RAppInstance, now: datetime.datetime | None = None) -> bool:
    """Rolls an unresolved upgrade back once its deadline has passed (reason
    UPGRADE_TIMEOUT). Returns True if it did. A pending pointer whose
    replacement row no longer exists is rolled back too."""
    if old.pending_upgrade_instance_id is None or InstanceState(old.state) != InstanceState.UPGRADING:
        return False
    new = db.get(RAppInstance, old.pending_upgrade_instance_id)
    now = now or datetime.datetime.now(datetime.UTC)
    if new is not None and upgrade_deadline(old, new) > now:
        return False
    _roll_back(db, old, new, "UPGRADE_TIMEOUT")
    return True
