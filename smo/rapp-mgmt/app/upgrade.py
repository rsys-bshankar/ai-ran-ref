"""UpgradeInstance orchestration — Onboarding/rApp Mgmt LLD section 6.

Coordinates two RAppInstance rows through statemachine.py's per-row FSM.
Kept separate from the FSM itself because this is cross-entity choreography
(a saga), not a single state machine — same separation used for the
NFO -> FOCOM dependency in the NFO+FOCOM LLD.
"""

import uuid

from sqlalchemy.orm import Session

from .models import RAppInstance
from .statemachine import RAPP_INSTANCE_FSM, InstanceEvent, InstanceState


def start_upgrade(db: Session, old: RAppInstance, new_package_id: uuid.UUID) -> RAppInstance:
    old.state = RAPP_INSTANCE_FSM.fire(InstanceState(old.state), InstanceEvent.START_UPGRADE, instance=old)
    new = RAppInstance(package_id=new_package_id, state=InstanceState.DEPLOYING)
    db.add(new)
    db.flush()
    old.pending_upgrade_instance_id = new.instance_id
    return new


def resolve_upgrade(db: Session, old: RAppInstance, new: RAppInstance, new_bootstrap_succeeded: bool) -> None:
    """Called once the new instance's bootstrap outcome is known — either
    it reached RUNNING within old.upgrade_timeout_seconds, or it didn't
    (FAULTED or the timeout fired first). Auto-rollback, no manual
    intervention, per v1.3's own decision — this function is where that
    decision is actually executed.
    """
    if new_bootstrap_succeeded:
        new.state = RAPP_INSTANCE_FSM.fire(InstanceState.DEPLOYING, InstanceEvent.BOOTSTRAP_OK, instance=new)
        old.state = RAPP_INSTANCE_FSM.fire(InstanceState(old.state), InstanceEvent.UPGRADE_COMMIT, instance=old)
        db.delete(old)  # old row torn down once its replacement is confirmed RUNNING
    else:
        new.state = RAPP_INSTANCE_FSM.fire(InstanceState.DEPLOYING, InstanceEvent.BOOTSTRAP_FAILED, instance=new)
        db.delete(new)  # failed replacement torn down
        old.state = RAPP_INSTANCE_FSM.fire(InstanceState(old.state), InstanceEvent.UPGRADE_ROLLBACK, instance=old)
        old.pending_upgrade_instance_id = None
