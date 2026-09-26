"""Two lifecycles plus one policy function, per AI/ML Workflow LLD:
  - AIMLModel     (SMO Design v1.3 section 3.8's state diagram, unchanged
                    shape — retraining re-enters at TRAINING, not REGISTERED,
                    since the model identity/registration itself doesn't change)
  - InferenceJob  (LLD section 3 — new, v1.3 never modeled serving at all)
  - retrain propagation (LLD section 4.3-4.4 — the ANY_MEMBER_TRIGGERS
    decision, made computable rather than just described)
"""

from __future__ import annotations

from enum import StrEnum

from smo_shared.statemachine import StateMachine

# ---------------------------------------------------------------- AIMLModel

class ModelState(StrEnum):
    REGISTERED = "REGISTERED"
    TRAINING = "TRAINING"
    TESTED = "TESTED"
    EMULATED = "EMULATED"
    CERTIFIED = "CERTIFIED"
    LOADED = "LOADED"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"


class ModelEvent(StrEnum):
    TRAIN = "TRAIN"
    TRAINING_COMPLETE = "TRAINING_COMPLETE"    # -> TESTED (MLVF)
    VALIDATION_COMPLETE = "VALIDATION_COMPLETE"  # -> EMULATED (MLEF)
    CERTIFY = "CERTIFY"                          # AIMgF governance decision -> CERTIFIED
    LOAD = "LOAD"                                # MLLF deploys -> LOADED
    ACTIVATE = "ACTIVATE"                        # -> ACTIVE
    RETRAIN = "RETRAIN"                          # ACTIVE -> TRAINING, full pipeline re-entry
    DEPRECATE = "DEPRECATE"


def build_aiml_model_fsm() -> StateMachine[ModelState, ModelEvent]:
    fsm: StateMachine[ModelState, ModelEvent] = StateMachine()
    fsm.add(ModelState.REGISTERED, ModelEvent.TRAIN, ModelState.TRAINING)
    fsm.add(ModelState.TRAINING, ModelEvent.TRAINING_COMPLETE, ModelState.TESTED)
    fsm.add(ModelState.TESTED, ModelEvent.VALIDATION_COMPLETE, ModelState.EMULATED)
    fsm.add(ModelState.EMULATED, ModelEvent.CERTIFY, ModelState.CERTIFIED)
    fsm.add(ModelState.CERTIFIED, ModelEvent.LOAD, ModelState.LOADED)
    fsm.add(ModelState.LOADED, ModelEvent.ACTIVATE, ModelState.ACTIVE)
    # No lightweight update path — existing project design principle,
    # confirmed unchanged by every LLD pass touching this module.
    fsm.add(ModelState.ACTIVE, ModelEvent.RETRAIN, ModelState.TRAINING)
    fsm.add(ModelState.ACTIVE, ModelEvent.DEPRECATE, ModelState.DEPRECATED)
    return fsm


AIML_MODEL_FSM = build_aiml_model_fsm()

# ---------------------------------------------------------------- InferenceJob

class InferenceState(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class InferenceEvent(StrEnum):
    COMPLETE = "COMPLETE"   # signals result is now pullable via DME (section 3)
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
