"""RAppInstance lifecycle.

SMO Design v1.3 section 3.5 state diagram, made precise by Onboarding/rApp
Mgmt LLD section 6: UpgradeInstance is TWO rows in choreography (old kept
running, new deployed alongside it), not one row transitioning through an
'upgrading' state in place. The FSM below governs each row's own
transitions; perform_upgrade() in main.py is the orchestrator that
coordinates both rows and applies the timeout/auto-rollback policy.
"""

from __future__ import annotations

from enum import StrEnum

from smo_shared.statemachine import StateMachine

from .models import RAppInstance


class InstanceState(StrEnum):
    DEPLOYING = "DEPLOYING"
    RUNNING = "RUNNING"
    UPGRADING = "UPGRADING"
    TERMINATING = "TERMINATING"
    FAULTED = "FAULTED"


class InstanceEvent(StrEnum):
    BOOTSTRAP_OK = "BOOTSTRAP_OK"
    BOOTSTRAP_FAILED = "BOOTSTRAP_FAILED"
    START_UPGRADE = "START_UPGRADE"
    UPGRADE_COMMIT = "UPGRADE_COMMIT"      # this row's replacement succeeded — this row is going away
    UPGRADE_ROLLBACK = "UPGRADE_ROLLBACK"  # replacement failed/timed out — this row stays, unaffected
    TERMINATE = "TERMINATE"
    CRASH = "CRASH"
    RECOVER = "RECOVER"                    # manual recovery / re-deploy, per v1.3's own FAULTED exit


def _revoke_credential(instance: RAppInstance, **_) -> None:
    """Credential revocation as an explicit, required sub-step of
    TerminateInstance — not a separate operation (closes v1.3's RT-3
    red-team finding).
    """
    instance.oauth_client_id = None


def build_rapp_instance_fsm() -> StateMachine[InstanceState, InstanceEvent]:
    fsm: StateMachine[InstanceState, InstanceEvent] = StateMachine()
    fsm.add(InstanceState.DEPLOYING, InstanceEvent.BOOTSTRAP_OK, InstanceState.RUNNING)
    fsm.add(InstanceState.DEPLOYING, InstanceEvent.BOOTSTRAP_FAILED, InstanceState.FAULTED)
    fsm.add(InstanceState.RUNNING, InstanceEvent.START_UPGRADE, InstanceState.UPGRADING)
    fsm.add(InstanceState.UPGRADING, InstanceEvent.UPGRADE_COMMIT, InstanceState.TERMINATING, action=_revoke_credential)
    fsm.add(InstanceState.UPGRADING, InstanceEvent.UPGRADE_ROLLBACK, InstanceState.RUNNING)
    fsm.add(InstanceState.RUNNING, InstanceEvent.TERMINATE, InstanceState.TERMINATING, action=_revoke_credential)
    fsm.add(InstanceState.RUNNING, InstanceEvent.CRASH, InstanceState.FAULTED)
    fsm.add(InstanceState.FAULTED, InstanceEvent.RECOVER, InstanceState.DEPLOYING)
    return fsm


RAPP_INSTANCE_FSM = build_rapp_instance_fsm()
