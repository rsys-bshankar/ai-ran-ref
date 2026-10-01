"""Tests for AIMgF's own two real state machines (Wave 2, AI Platform
Service Decomposition) — ModelLifecycle (14 states, a model's own
identity/certification path) and RuntimeLifecycle (8 states, its serving
existence, jointly owned with NFO). Run with: pytest smo/aimgf/tests -q
"""

import pytest

from smo_shared.statemachine import IllegalTransition

from app.statemachine import (
    GOVERNANCE_EVENTS,
    INFERENCE_JOB_FSM,
    MODEL_LIFECYCLE_FSM,
    RUNTIME_LIFECYCLE_FSM,
    InferenceEvent,
    InferenceState,
    ModelLifecycleEvent,
    ModelLifecycleState,
    RuntimeLifecycleEvent,
    RuntimeLifecycleState,
    should_trigger_group_retrain,
)


# ---------------------------------------------------------------- ModelLifecycle

def test_full_certification_pipeline():
    s = ModelLifecycleState.REGISTERED
    for event, expected in [
        (ModelLifecycleEvent.CREATE_TRAINING, ModelLifecycleState.TRAINING),
        (ModelLifecycleEvent.TRAINING_COMPLETE, ModelLifecycleState.TRAINED),
        (ModelLifecycleEvent.CREATE_VALIDATION, ModelLifecycleState.VALIDATING),
        (ModelLifecycleEvent.VALIDATION_COMPLETE, ModelLifecycleState.VALIDATED),
        (ModelLifecycleEvent.CREATE_EMULATION, ModelLifecycleState.EMULATING),
        (ModelLifecycleEvent.EMULATION_COMPLETE, ModelLifecycleState.EMULATED),
        (ModelLifecycleEvent.SUBMIT_FOR_APPROVAL, ModelLifecycleState.PENDING_APPROVAL),
        (ModelLifecycleEvent.APPROVE, ModelLifecycleState.APPROVED),
        (ModelLifecycleEvent.CERTIFY, ModelLifecycleState.CERTIFIED),
        (ModelLifecycleEvent.PROMOTE, ModelLifecycleState.PROMOTED),
    ]:
        s = MODEL_LIFECYCLE_FSM.fire(s, event)
        assert s == expected


def test_no_shortcut_from_registered_to_certified():
    """No lightweight update path — existing project design principle,
    carried over unchanged from Wave 1. Skipping any intermediate phase
    must fail.
    """
    with pytest.raises(IllegalTransition):
        MODEL_LIFECYCLE_FSM.fire(ModelLifecycleState.REGISTERED, ModelLifecycleEvent.CERTIFY)
    with pytest.raises(IllegalTransition):
        MODEL_LIFECYCLE_FSM.fire(ModelLifecycleState.TRAINED, ModelLifecycleEvent.SUBMIT_FOR_APPROVAL)


def test_retraining_a_promoted_model_re_enters_at_training_not_registered():
    s = MODEL_LIFECYCLE_FSM.fire(ModelLifecycleState.PROMOTED, ModelLifecycleEvent.CREATE_TRAINING)
    assert s == ModelLifecycleState.TRAINING
    with pytest.raises(IllegalTransition):
        MODEL_LIFECYCLE_FSM.fire(s, ModelLifecycleEvent.SUBMIT_FOR_APPROVAL)


def test_rollback_demotes_a_promoted_model_to_certified_not_further():
    s = MODEL_LIFECYCLE_FSM.fire(ModelLifecycleState.PROMOTED, ModelLifecycleEvent.ROLLBACK)
    assert s == ModelLifecycleState.CERTIFIED
    with pytest.raises(IllegalTransition):
        MODEL_LIFECYCLE_FSM.fire(s, ModelLifecycleEvent.ROLLBACK)


def test_reject_during_approval_routes_to_failed_not_a_dead_end():
    s = MODEL_LIFECYCLE_FSM.fire(ModelLifecycleState.PENDING_APPROVAL, ModelLifecycleEvent.REJECT)
    assert s == ModelLifecycleState.FAILED
    # a failure can be retried from the top of the pipeline
    assert MODEL_LIFECYCLE_FSM.fire(s, ModelLifecycleEvent.CREATE_TRAINING) == ModelLifecycleState.TRAINING


def test_deprecate_from_certified_or_promoted():
    assert MODEL_LIFECYCLE_FSM.fire(ModelLifecycleState.CERTIFIED, ModelLifecycleEvent.DEPRECATE) == ModelLifecycleState.DEPRECATED
    assert MODEL_LIFECYCLE_FSM.fire(ModelLifecycleState.PROMOTED, ModelLifecycleEvent.DEPRECATE) == ModelLifecycleState.DEPRECATED


def test_retire_from_deprecated_or_failed():
    assert MODEL_LIFECYCLE_FSM.fire(ModelLifecycleState.DEPRECATED, ModelLifecycleEvent.RETIRE) == ModelLifecycleState.RETIRED
    assert MODEL_LIFECYCLE_FSM.fire(ModelLifecycleState.FAILED, ModelLifecycleEvent.RETIRE) == ModelLifecycleState.RETIRED


def test_retired_is_terminal():
    with pytest.raises(IllegalTransition):
        MODEL_LIFECYCLE_FSM.fire(ModelLifecycleState.RETIRED, ModelLifecycleEvent.CREATE_TRAINING)


def test_governance_events_are_exactly_the_documented_four_decisions_plus_their_framing():
    """docs/ARCHITECTURE.md's AIMgF Governance list: Approval, Certification,
    Promotion, Rollback — plus the submit/reject pair framing approval,
    plus (HISTORY.md OI-6.1) the Training/Validation operator
    gate's own two decisions — so the CertificationRecord audit trail
    covers the whole governance conversation main.py writes it for.
    """
    assert GOVERNANCE_EVENTS == {
        ModelLifecycleEvent.SUBMIT_FOR_APPROVAL, ModelLifecycleEvent.APPROVE, ModelLifecycleEvent.REJECT,
        ModelLifecycleEvent.CERTIFY, ModelLifecycleEvent.PROMOTE, ModelLifecycleEvent.ROLLBACK,
        ModelLifecycleEvent.APPROVE_TRAINING, ModelLifecycleEvent.APPROVE_VALIDATION,
    }


def test_model_lifecycle_state_has_exactly_fourteen_states():
    assert len(list(ModelLifecycleState)) == 14


# ---------------------------------------------------------------- RuntimeLifecycle

def test_runtime_lifecycle_state_has_exactly_eight_states():
    assert len(list(RuntimeLifecycleState)) == 8


def test_full_deploy_activate_scale_terminate_pipeline():
    s = RuntimeLifecycleState.NOT_DEPLOYED
    for event, expected in [
        (RuntimeLifecycleEvent.REQUEST_DEPLOYMENT, RuntimeLifecycleState.DEPLOYMENT_REQUESTED),
        (RuntimeLifecycleEvent.DEPLOYMENT_COMPLETE, RuntimeLifecycleState.DEPLOYED),
        (RuntimeLifecycleEvent.ACTIVATE, RuntimeLifecycleState.ACTIVATING),
        (RuntimeLifecycleEvent.ACTIVATION_COMPLETE, RuntimeLifecycleState.ACTIVE),
        (RuntimeLifecycleEvent.REQUEST_SCALE, RuntimeLifecycleState.SCALING),
        (RuntimeLifecycleEvent.SCALE_COMPLETE, RuntimeLifecycleState.ACTIVE),
        (RuntimeLifecycleEvent.REQUEST_TERMINATION, RuntimeLifecycleState.TERMINATING),
        (RuntimeLifecycleEvent.TERMINATION_COMPLETE, RuntimeLifecycleState.TERMINATED),
    ]:
        s = RUNTIME_LIFECYCLE_FSM.fire(s, event)
        assert s == expected


def test_termination_is_legal_from_every_pre_active_deployed_state():
    for state in (RuntimeLifecycleState.DEPLOYMENT_REQUESTED, RuntimeLifecycleState.DEPLOYED, RuntimeLifecycleState.ACTIVE):
        assert RUNTIME_LIFECYCLE_FSM.fire(state, RuntimeLifecycleEvent.REQUEST_TERMINATION) == RuntimeLifecycleState.TERMINATING


def test_terminated_is_terminal():
    with pytest.raises(IllegalTransition):
        RUNTIME_LIFECYCLE_FSM.fire(RuntimeLifecycleState.TERMINATED, RuntimeLifecycleEvent.REQUEST_DEPLOYMENT)


def test_cannot_scale_before_active():
    with pytest.raises(IllegalTransition):
        RUNTIME_LIFECYCLE_FSM.fire(RuntimeLifecycleState.DEPLOYED, RuntimeLifecycleEvent.REQUEST_SCALE)


# ---------------------------------------------------------------- InferenceJob

def test_inference_completes():
    s = INFERENCE_JOB_FSM.fire(InferenceState.RUNNING, InferenceEvent.COMPLETE)
    assert s == InferenceState.COMPLETED


def test_inference_fails():
    s = INFERENCE_JOB_FSM.fire(InferenceState.RUNNING, InferenceEvent.FAIL)
    assert s == InferenceState.FAILED


def test_inference_terminal_states_have_no_further_transitions():
    with pytest.raises(IllegalTransition):
        INFERENCE_JOB_FSM.fire(InferenceState.COMPLETED, InferenceEvent.COMPLETE)


# ---------------------------------------------------------------- retrain propagation

def test_any_member_triggers_fires_on_single_breach():
    """The checked-in default (LLD section 4.4) for the evidenced rows
    12-15 SHARED_MODEL cluster: any one member's guard-KPI breach is a
    signal about the shared model, so it triggers immediately.
    """
    assert should_trigger_group_retrain("ANY_MEMBER_TRIGGERS", member_count=4, breached_count=1) is True


def test_any_member_triggers_does_not_fire_with_zero_breaches():
    assert should_trigger_group_retrain("ANY_MEMBER_TRIGGERS", member_count=4, breached_count=0) is False


def test_majority_triggers_requires_more_than_half():
    assert should_trigger_group_retrain("MAJORITY_TRIGGERS", member_count=4, breached_count=1) is False
    assert should_trigger_group_retrain("MAJORITY_TRIGGERS", member_count=4, breached_count=3) is True


def test_weighted_triggers_is_reserved_not_implemented():
    """Section 4.4's revisit trigger 3: WEIGHTED_TRIGGERS gets designed
    once real noise-floor data exists, not invented speculatively now.
    """
    with pytest.raises(NotImplementedError):
        should_trigger_group_retrain("WEIGHTED_TRIGGERS", member_count=4, breached_count=1)


def test_certified_model_can_be_retrained():
    """OI-2-training-lifecycle-edges: a rolled-back model (PROMOTED -ROLLBACK->
    CERTIFIED) re-enters training like a PROMOTED one."""
    s = MODEL_LIFECYCLE_FSM.fire(ModelLifecycleState.PROMOTED, ModelLifecycleEvent.ROLLBACK)
    assert MODEL_LIFECYCLE_FSM.fire(s, ModelLifecycleEvent.CREATE_TRAINING) == ModelLifecycleState.TRAINING


def test_advanceable_events_are_governance_plus_end_of_life_only():
    from app.statemachine import ADVANCEABLE_EVENTS
    assert ADVANCEABLE_EVENTS == GOVERNANCE_EVENTS | {ModelLifecycleEvent.DEPRECATE, ModelLifecycleEvent.RETIRE}
    for job_driven in ("CREATE_TRAINING", "TRAINING_COMPLETE", "TRAINING_FAILED", "CREATE_VALIDATION",
                       "VALIDATION_COMPLETE", "VALIDATION_FAILED", "CREATE_EMULATION", "EMULATION_COMPLETE",
                       "EMULATION_FAILED"):
        assert ModelLifecycleEvent(job_driven) not in ADVANCEABLE_EVENTS


def test_trainable_states_match_the_create_training_edges():
    from app.statemachine import TRAINABLE_STATES
    with_edge = {s for s in ModelLifecycleState if ModelLifecycleEvent.CREATE_TRAINING in MODEL_LIFECYCLE_FSM.legal_events(s)}
    assert with_edge == {ModelLifecycleState.REGISTERED, ModelLifecycleState.CERTIFIED, ModelLifecycleState.PROMOTED,
                         ModelLifecycleState.FAILED}
    # plus TRAINING itself: a new request supersedes the in-flight run (main.py _start_training)
    assert TRAINABLE_STATES == with_edge | {ModelLifecycleState.TRAINING}
