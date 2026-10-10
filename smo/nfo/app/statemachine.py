"""The NFDeployment lifecycle: its states, its events and the transition table (`NFO_FSM`).

Used by `main.py`, which calls `NFO_FSM.fire(state, event)` for every state change and turns `IllegalTransition` into a 409.
The states keep this build's names (INSTANTIATING, RUNNING) rather than the reference implementation's (Installing,
Installed); the transitions follow the reference's O2 DMS dispatch (HISTORY.md §5), and `OI-3-nfo-abnormal` added the
events the deployment manager reports. DELETE_COMPLETE is not an event here: it ends the deployment, which the route handles.

Before editing: a new transition is a line in `build_nfo_fsm`; a route that fires an event with no edge from the current state
answers 409.
"""

from __future__ import annotations

from enum import StrEnum

from smo_shared.statemachine import StateMachine


class DeploymentState(StrEnum):
    """States of a deployment. INITIAL is only the moment of creation; DELETING and ABNORMAL are reached through Terminate or a
    deployment manager report.
    """
    INITIAL = "INITIAL"
    INSTANTIATING = "INSTANTIATING"
    RUNNING = "RUNNING"
    UPDATING = "UPDATING"
    TERMINATING = "TERMINATING"
    ABNORMAL = "ABNORMAL"
    DELETING = "DELETING"


class DeploymentEvent(StrEnum):
    """Events that move a deployment: the SMO's own operations, and the reports of the deployment manager (UNINSTALL_*,
    DELETE_FAILED, RUNTIME_FAILURE).
    """
    INSTANTIATE = "INSTANTIATE"
    INSTANTIATE_COMPLETE = "INSTANTIATE_COMPLETE"
    UPDATE = "UPDATE"
    UPDATE_COMPLETE = "UPDATE_COMPLETE"
    HEAL = "HEAL"
    TERMINATE = "TERMINATE"
    # OI-3-nfo-abnormal: what the deployment manager (O2 DMS) reports back —
    # the end of an asynchronous uninstall and of the resource deletion after
    # it, or a workload that broke at runtime.
    UNINSTALL_COMPLETE = "UNINSTALL_COMPLETE"
    UNINSTALL_FAILED = "UNINSTALL_FAILED"
    DELETE_FAILED = "DELETE_FAILED"
    RUNTIME_FAILURE = "RUNTIME_FAILURE"


def build_nfo_fsm() -> StateMachine[DeploymentState, DeploymentEvent]:
    """Builds the transition table. Reading guide: INSTANTIATE and UPDATE each have a completion event that the route fires in the
    same request; HEAL recovers ABNORMAL; TERMINATE has an edge from every state (so the route never meets an illegal
    Terminate) with the targets explained in the comments below; the last block holds the deployment manager's reports.
    """
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

    # OI-3-nfo-abnormal: uninstalled -> DELETING (its O-Cloud resources are
    # released, then the record goes: DELETE_COMPLETE, main.py), or a failure
    # at either stage -> ABNORMAL, from where Terminate retries and Heal
    # recovers. A running workload that breaks is ABNORMAL too.
    fsm.add(DeploymentState.TERMINATING, DeploymentEvent.UNINSTALL_COMPLETE, DeploymentState.DELETING)
    fsm.add(DeploymentState.TERMINATING, DeploymentEvent.UNINSTALL_FAILED, DeploymentState.ABNORMAL)
    fsm.add(DeploymentState.DELETING, DeploymentEvent.DELETE_FAILED, DeploymentState.ABNORMAL)
    for state in (DeploymentState.INSTANTIATING, DeploymentState.RUNNING, DeploymentState.UPDATING):
        fsm.add(state, DeploymentEvent.RUNTIME_FAILURE, DeploymentState.ABNORMAL)
    return fsm


NFO_FSM = build_nfo_fsm()
