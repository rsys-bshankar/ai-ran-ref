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

import httpx

from smo_shared.r1_client import R1Client
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


def _reconsider_dme_registration(instance: RAppInstance, **_) -> None:
    """rApp-as-producer reconsideration trigger (OPEN_ITEMS.md section 1):
    when this instance crashes or terminates it can no longer be trusted
    as a live DME producer, so its own DME registrations — keyed by
    producer_id, which is this instance's oauth_client_id (bootstrap
    registers with SME/DME using that same identity) — are deregistered
    here. Best-effort: a DME outage must never block CRASH/TERMINATE
    themselves, the same "unreachable callback never fails the primary
    operation" precedent Policy Mgmt's CreateIntent dispatch uses.
    """
    if instance.oauth_client_id is None:
        return
    try:
        R1Client().delete("/dme/production-capabilities", params={"producer_id": instance.oauth_client_id})
    except httpx.HTTPError:
        pass


def _terminate_side_effects(instance: RAppInstance, **_) -> None:
    # DME reconsideration must run BEFORE credential revocation — it reads
    # instance.oauth_client_id as the DME producer_id, which
    # _revoke_credential clears to None.
    _reconsider_dme_registration(instance)
    _revoke_credential(instance)


def build_rapp_instance_fsm() -> StateMachine[InstanceState, InstanceEvent]:
    fsm: StateMachine[InstanceState, InstanceEvent] = StateMachine()
    fsm.add(InstanceState.DEPLOYING, InstanceEvent.BOOTSTRAP_OK, InstanceState.RUNNING)
    fsm.add(InstanceState.DEPLOYING, InstanceEvent.BOOTSTRAP_FAILED, InstanceState.FAULTED)
    fsm.add(InstanceState.RUNNING, InstanceEvent.START_UPGRADE, InstanceState.UPGRADING)
    fsm.add(InstanceState.UPGRADING, InstanceEvent.UPGRADE_COMMIT, InstanceState.TERMINATING, action=_revoke_credential)
    fsm.add(InstanceState.UPGRADING, InstanceEvent.UPGRADE_ROLLBACK, InstanceState.RUNNING)
    fsm.add(InstanceState.RUNNING, InstanceEvent.TERMINATE, InstanceState.TERMINATING, action=_terminate_side_effects)
    fsm.add(InstanceState.RUNNING, InstanceEvent.CRASH, InstanceState.FAULTED, action=_reconsider_dme_registration)
    fsm.add(InstanceState.FAULTED, InstanceEvent.RECOVER, InstanceState.DEPLOYING)
    return fsm


RAPP_INSTANCE_FSM = build_rapp_instance_fsm()
