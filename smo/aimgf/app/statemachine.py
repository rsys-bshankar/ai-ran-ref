"""Wave 2 (AI Platform Service Decomposition): AIMgF's own two real state
machines, per docs/ARCHITECTURE.md (AIMgF) — replacing Wave 1's single
flat `ModelState`/`ModelEvent` (which conflated a model's own identity/
certification progress with its runtime/serving existence, and lived partly
on MLMR's row via `PATCH /models/{id}/lifecycle`).

  - ModelLifecycle  (14 states) — a model's own identity/certification
                      path: training -> validation -> emulation ->
                      governance (approval/certification/promotion) ->
                      deprecation/retirement. AIMgF's own truth end to end
                      (docs/ARCHITECTURE.md:
                      "Lifecycle state: AIMgF ✅, MLMR ❌").
  - RuntimeLifecycle (8 states) — a model's serving existence once
                      PROMOTED, jointly owned with NFO (NFO invocation:
                      request runtime creation/termination/scaling).
                      Decoupled from ModelLifecycle so retraining a
                      PROMOTED model doesn't force its runtime down, and a
                      runtime can be scaled/terminated without touching
                      the model's own certification state.

Also carries `should_trigger_group_retrain`, unchanged from Wave 1 (AI/ML
Workflow LLD section 4.3-4.4's retrain-propagation decision).
"""

from __future__ import annotations

from enum import StrEnum

from smo_shared.statemachine import StateMachine

# ---------------------------------------------------------------- ModelLifecycle

class ModelLifecycleState(StrEnum):
    REGISTERED = "REGISTERED"
    TRAINING = "TRAINING"
    TRAINED = "TRAINED"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    EMULATING = "EMULATING"
    EMULATED = "EMULATED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    CERTIFIED = "CERTIFIED"
    PROMOTED = "PROMOTED"
    DEPRECATED = "DEPRECATED"
    RETIRED = "RETIRED"
    FAILED = "FAILED"


class ModelLifecycleEvent(StrEnum):
    CREATE_TRAINING = "CREATE_TRAINING"            # REGISTERED/PROMOTED/FAILED -> TRAINING (first cycle or retrain)
    TRAINING_COMPLETE = "TRAINING_COMPLETE"          # -> TRAINED
    TRAINING_FAILED = "TRAINING_FAILED"                # -> FAILED
    # HISTORY.md OI-6.1: an explicit operator-approval gate,
    # mirroring the existing CERTIFY/PROMOTE governance shape — a real,
    # decidedBy-carrying event with its own CertificationRecord, not a
    # bare state check. Self-loops (TRAINED->TRAINED / VALIDATED->
    # VALIDATED): the model's own ModelLifecycleState doesn't change,
    # only ModelLifecycle.training_approved/validation_approved, which
    # CREATE_VALIDATION/CREATE_EMULATION now gate on (main.py).
    APPROVE_TRAINING = "APPROVE_TRAINING"                # TRAINED -> TRAINED (gates CREATE_VALIDATION)
    CREATE_VALIDATION = "CREATE_VALIDATION"              # TRAINED -> VALIDATING (MLVF)
    VALIDATION_COMPLETE = "VALIDATION_COMPLETE"            # -> VALIDATED
    VALIDATION_FAILED = "VALIDATION_FAILED"                  # -> FAILED
    APPROVE_VALIDATION = "APPROVE_VALIDATION"                  # VALIDATED -> VALIDATED (gates CREATE_EMULATION)
    CREATE_EMULATION = "CREATE_EMULATION"                      # VALIDATED -> EMULATING (MLEF)
    EMULATION_COMPLETE = "EMULATION_COMPLETE"                    # -> EMULATED
    EMULATION_FAILED = "EMULATION_FAILED"                          # -> FAILED
    SUBMIT_FOR_APPROVAL = "SUBMIT_FOR_APPROVAL"                      # EMULATED -> PENDING_APPROVAL (governance)
    APPROVE = "APPROVE"                                                # -> APPROVED
    REJECT = "REJECT"                                                    # PENDING_APPROVAL -> FAILED
    CERTIFY = "CERTIFY"                                                    # APPROVED -> CERTIFIED
    PROMOTE = "PROMOTE"                                                      # CERTIFIED -> PROMOTED
    ROLLBACK = "ROLLBACK"                                                      # PROMOTED -> CERTIFIED (governance)
    DEPRECATE = "DEPRECATE"                                                      # CERTIFIED/PROMOTED -> DEPRECATED
    RETIRE = "RETIRE"                                                              # DEPRECATED/FAILED -> RETIRED


# Governance events a CertificationRecord is written for (main.py's
# advance_model_lifecycle) — Approval/Certification/Promotion/Rollback
# per docs/ARCHITECTURE.md's AIMgF Governance list, plus the two decisions
# framing approval (submit/reject) so the audit trail covers the whole
# governance conversation, not just its middle.
GOVERNANCE_EVENTS = frozenset({
    ModelLifecycleEvent.SUBMIT_FOR_APPROVAL, ModelLifecycleEvent.APPROVE, ModelLifecycleEvent.REJECT,
    ModelLifecycleEvent.CERTIFY, ModelLifecycleEvent.PROMOTE, ModelLifecycleEvent.ROLLBACK,
    # HISTORY.md OI-6.1
    ModelLifecycleEvent.APPROVE_TRAINING, ModelLifecycleEvent.APPROVE_VALIDATION,
})


def build_model_lifecycle_fsm() -> StateMachine[ModelLifecycleState, ModelLifecycleEvent]:
    fsm: StateMachine[ModelLifecycleState, ModelLifecycleEvent] = StateMachine()
    S, E = ModelLifecycleState, ModelLifecycleEvent
    fsm.add(S.REGISTERED, E.CREATE_TRAINING, S.TRAINING)
    fsm.add(S.TRAINING, E.TRAINING_COMPLETE, S.TRAINED)
    fsm.add(S.TRAINING, E.TRAINING_FAILED, S.FAILED)
    fsm.add(S.TRAINED, E.APPROVE_TRAINING, S.TRAINED)  # HISTORY.md OI-6.1 — operator gate, no state change
    fsm.add(S.TRAINED, E.CREATE_VALIDATION, S.VALIDATING)
    fsm.add(S.VALIDATING, E.VALIDATION_COMPLETE, S.VALIDATED)
    fsm.add(S.VALIDATING, E.VALIDATION_FAILED, S.FAILED)
    fsm.add(S.VALIDATED, E.APPROVE_VALIDATION, S.VALIDATED)  # HISTORY.md OI-6.1 — operator gate, no state change
    fsm.add(S.VALIDATED, E.CREATE_EMULATION, S.EMULATING)
    fsm.add(S.EMULATING, E.EMULATION_COMPLETE, S.EMULATED)
    fsm.add(S.EMULATING, E.EMULATION_FAILED, S.FAILED)
    fsm.add(S.EMULATED, E.SUBMIT_FOR_APPROVAL, S.PENDING_APPROVAL)
    fsm.add(S.PENDING_APPROVAL, E.APPROVE, S.APPROVED)
    fsm.add(S.PENDING_APPROVAL, E.REJECT, S.FAILED)
    fsm.add(S.APPROVED, E.CERTIFY, S.CERTIFIED)
    fsm.add(S.CERTIFIED, E.PROMOTE, S.PROMOTED)
    fsm.add(S.CERTIFIED, E.DEPRECATE, S.DEPRECATED)
    fsm.add(S.PROMOTED, E.ROLLBACK, S.CERTIFIED)
    fsm.add(S.PROMOTED, E.DEPRECATE, S.DEPRECATED)
    # No lightweight update path — existing project design principle
    # carried over from Wave 1's own ACTIVE -> RETRAIN -> TRAINING edge:
    # retraining a PROMOTED model re-enters the full pipeline at TRAINING,
    # not at REGISTERED (identity/registration itself doesn't change).
    fsm.add(S.PROMOTED, E.CREATE_TRAINING, S.TRAINING)
    fsm.add(S.DEPRECATED, E.RETIRE, S.RETIRED)
    fsm.add(S.FAILED, E.RETIRE, S.RETIRED)
    fsm.add(S.FAILED, E.CREATE_TRAINING, S.TRAINING)  # retry after a failure, same re-entry point
    return fsm


MODEL_LIFECYCLE_FSM = build_model_lifecycle_fsm()

# ---------------------------------------------------------------- RuntimeLifecycle

class RuntimeLifecycleState(StrEnum):
    NOT_DEPLOYED = "NOT_DEPLOYED"
    DEPLOYMENT_REQUESTED = "DEPLOYMENT_REQUESTED"
    DEPLOYED = "DEPLOYED"
    ACTIVATING = "ACTIVATING"
    ACTIVE = "ACTIVE"
    SCALING = "SCALING"
    TERMINATING = "TERMINATING"
    TERMINATED = "TERMINATED"


class RuntimeLifecycleEvent(StrEnum):
    REQUEST_DEPLOYMENT = "REQUEST_DEPLOYMENT"          # NFO: request runtime creation
    DEPLOYMENT_COMPLETE = "DEPLOYMENT_COMPLETE"
    ACTIVATE = "ACTIVATE"
    ACTIVATION_COMPLETE = "ACTIVATION_COMPLETE"
    REQUEST_SCALE = "REQUEST_SCALE"                      # NFO: request runtime scaling
    SCALE_COMPLETE = "SCALE_COMPLETE"
    REQUEST_TERMINATION = "REQUEST_TERMINATION"            # NFO: request runtime termination
    TERMINATION_COMPLETE = "TERMINATION_COMPLETE"


def build_runtime_lifecycle_fsm() -> StateMachine[RuntimeLifecycleState, RuntimeLifecycleEvent]:
    fsm: StateMachine[RuntimeLifecycleState, RuntimeLifecycleEvent] = StateMachine()
    S, E = RuntimeLifecycleState, RuntimeLifecycleEvent
    fsm.add(S.NOT_DEPLOYED, E.REQUEST_DEPLOYMENT, S.DEPLOYMENT_REQUESTED)
    fsm.add(S.DEPLOYMENT_REQUESTED, E.DEPLOYMENT_COMPLETE, S.DEPLOYED)
    fsm.add(S.DEPLOYED, E.ACTIVATE, S.ACTIVATING)
    fsm.add(S.ACTIVATING, E.ACTIVATION_COMPLETE, S.ACTIVE)
    fsm.add(S.ACTIVE, E.REQUEST_SCALE, S.SCALING)
    fsm.add(S.SCALING, E.SCALE_COMPLETE, S.ACTIVE)
    fsm.add(S.DEPLOYMENT_REQUESTED, E.REQUEST_TERMINATION, S.TERMINATING)
    fsm.add(S.DEPLOYED, E.REQUEST_TERMINATION, S.TERMINATING)
    fsm.add(S.ACTIVE, E.REQUEST_TERMINATION, S.TERMINATING)
    fsm.add(S.TERMINATING, E.TERMINATION_COMPLETE, S.TERMINATED)
    return fsm


RUNTIME_LIFECYCLE_FSM = build_runtime_lifecycle_fsm()

# ---------------------------------------------------------------- InferenceJob

class InferenceState(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class InferenceEvent(StrEnum):
    COMPLETE = "COMPLETE"   # signals result is now pullable via DME
    FAIL = "FAIL"


def build_inference_job_fsm() -> StateMachine[InferenceState, InferenceEvent]:
    fsm: StateMachine[InferenceState, InferenceEvent] = StateMachine()
    fsm.add(InferenceState.RUNNING, InferenceEvent.COMPLETE, InferenceState.COMPLETED)
    fsm.add(InferenceState.RUNNING, InferenceEvent.FAIL, InferenceState.FAILED)
    return fsm


INFERENCE_JOB_FSM = build_inference_job_fsm()

# ---------------------------------------------------------------- retrain propagation

def should_trigger_group_retrain(retrain_propagation: str, member_count: int, breached_count: int) -> bool:
    """AI/ML Workflow LLD section 4.4's decision, made computable.
    ANY_MEMBER_TRIGGERS is the checked-in default for all SHARED_MODEL
    groups; MAJORITY_TRIGGERS and WEIGHTED_TRIGGERS are reserved policy
    values with named revisit triggers, not yet exercised by any real
    use case in this reference build.
    """
    if breached_count == 0:
        return False
    if retrain_propagation == "ANY_MEMBER_TRIGGERS":
        return True
    if retrain_propagation == "MAJORITY_TRIGGERS":
        return breached_count > member_count / 2
    if retrain_propagation == "WEIGHTED_TRIGGERS":
        raise NotImplementedError("WEIGHTED_TRIGGERS is reserved, undesigned — needs real noise-floor data first (LLD section 4.4)")
    raise ValueError(f"unknown retrainPropagation value: {retrain_propagation!r}")
