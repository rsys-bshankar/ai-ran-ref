"""NFDeployment lifecycle.

OPEN_ITEMS.md section 5: the reference's real NfDeploymentState
(o2dms/domain/states.py) has 7 states — Initial/Installing/Installed/
Updating/Uninstalling/Abnormal/Deleting — plus dispatch logic in
dms_lcm_nfdeployment.py's lcm_nfdeployment_uninstall; this build only
ever moved INSTANTIATING->RUNNING. State names below keep this build's
own existing vocabulary (INSTANTIATING/RUNNING, not Installing/Installed)
rather than adopting the reference's, matching how other modules in this
build (e.g. onboarding's AVAILABLE for the reference's COMMISSIONED)
already handle the same naming choice.
"""

from __future__ import annotations

from enum import StrEnum

from smo_shared.statemachine import StateMachine


class DeploymentState(StrEnum):
    INITIAL = "INITIAL"
    INSTANTIATING = "INSTANTIATING"
    RUNNING = "RUNNING"
    UPDATING = "UPDATING"
    TERMINATING = "TERMINATING"
    ABNORMAL = "ABNORMAL"
    DELETING = "DELETING"


class DeploymentEvent(StrEnum):
    INSTANTIATE = "INSTANTIATE"
    INSTANTIATE_COMPLETE = "INSTANTIATE_COMPLETE"
    UPDATE = "UPDATE"
    UPDATE_COMPLETE = "UPDATE_COMPLETE"
    HEAL = "HEAL"
    TERMINATE = "TERMINATE"


def build_nfo_fsm() -> StateMachine[DeploymentState, DeploymentEvent]:
    fsm: StateMachine[DeploymentState, DeploymentEvent] = StateMachine()
    fsm.add(DeploymentState.INITIAL, DeploymentEvent.INSTANTIATE, DeploymentState.INSTANTIATING)
    fsm.add(DeploymentState.INSTANTIATING, DeploymentEvent.INSTANTIATE_COMPLETE, DeploymentState.RUNNING)

    # Scale drives this edge (RequestTraining-style synchronous elision,
    # same pattern as Instantiate's own real-Helm-install elision): real
    # replica-count changes via Helm upgrade are out of scope, so both
    # transitions fire within one request rather than staying observably
    # UPDATING.
    fsm.add(DeploymentState.RUNNING, DeploymentEvent.UPDATE, DeploymentState.UPDATING)
    fsm.add(DeploymentState.UPDATING, DeploymentEvent.UPDATE_COMPLETE, DeploymentState.RUNNING)

    # Heal isn't explicitly modeled in the reference at all (no Heal
    # command exists in dms_lcm_nfdeployment.py) — this build's own
    # extrapolation to close "Heal has no state transitions of any kind":
    # a direct repair edge, not routed through Updating, since it isn't a
    # spec change. Idempotent from RUNNING (already healthy).
    fsm.add(DeploymentState.ABNORMAL, DeploymentEvent.HEAL, DeploymentState.RUNNING)
    fsm.add(DeploymentState.RUNNING, DeploymentEvent.HEAL, DeploymentState.RUNNING)

    # Terminate mirrors lcm_nfdeployment_uninstall's real state dispatch
    # exactly: INITIAL/ABNORMAL delete immediately (no chart was ever
    # installed, or it's already broken) — bypassing TERMINATING
    # entirely, matching the reference's direct
    # `uow.nfdeployments.delete(...)` calls from those two states.
    fsm.add(DeploymentState.INITIAL, DeploymentEvent.TERMINATE, DeploymentState.DELETING)
    fsm.add(DeploymentState.ABNORMAL, DeploymentEvent.TERMINATE, DeploymentState.DELETING)
    fsm.add(DeploymentState.INSTANTIATING, DeploymentEvent.TERMINATE, DeploymentState.TERMINATING)
    fsm.add(DeploymentState.RUNNING, DeploymentEvent.TERMINATE, DeploymentState.TERMINATING)
    fsm.add(DeploymentState.UPDATING, DeploymentEvent.TERMINATE, DeploymentState.TERMINATING)
    # Already mid-terminate: the reference's own `elif ... Uninstalling: pass`.
    fsm.add(DeploymentState.TERMINATING, DeploymentEvent.TERMINATE, DeploymentState.TERMINATING)
    # The reference's own defensive catch-all (`else: transit_state(Abnormal)`)
    # for a Terminate landing on a state its dispatch chain doesn't
    # otherwise handle — reachable here from DELETING, e.g. a
    # double-terminate race.
    fsm.add(DeploymentState.DELETING, DeploymentEvent.TERMINATE, DeploymentState.ABNORMAL)
    return fsm


NFO_FSM = build_nfo_fsm()
