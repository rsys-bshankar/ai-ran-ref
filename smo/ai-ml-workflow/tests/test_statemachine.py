"""Tests for AI/ML Workflow's lifecycles (AI/ML Workflow LLD sections 3-4).
Run with: pytest smo/ai-ml-workflow/tests -q
"""

import pytest

from smo_shared.statemachine import IllegalTransition

from app.statemachine import (
    AIML_MODEL_FSM,
    INFERENCE_JOB_FSM,
    InferenceEvent,
    InferenceState,
    ModelEvent,
    ModelState,
    should_trigger_group_retrain,
)


# ---------------------------------------------------------------- AIMLModel

def test_full_certification_pipeline():
    s = ModelState.REGISTERED
    for event, expected in [
        (ModelEvent.TRAIN, ModelState.TRAINING),
        (ModelEvent.TRAINING_COMPLETE, ModelState.TESTED),
        (ModelEvent.VALIDATION_COMPLETE, ModelState.EMULATED),
        (ModelEvent.CERTIFY, ModelState.CERTIFIED),
        (ModelEvent.LOAD, ModelState.LOADED),
        (ModelEvent.ACTIVATE, ModelState.ACTIVE),
    ]:
        s = AIML_MODEL_FSM.fire(s, event)
        assert s == expected


def test_no_shortcut_from_registered_to_certified():
    """No lightweight update path — confirmed, unchanged project design
    principle. Skipping TESTED/EMULATED must fail.
    """
    with pytest.raises(IllegalTransition):
        AIML_MODEL_FSM.fire(ModelState.REGISTERED, ModelEvent.CERTIFY)


def test_retraining_re_enters_at_training_not_registered():
    s = AIML_MODEL_FSM.fire(ModelState.ACTIVE, ModelEvent.RETRAIN)
    assert s == ModelState.TRAINING
    # and the full pipeline is required again — no fast path back to ACTIVE
    with pytest.raises(IllegalTransition):
        AIML_MODEL_FSM.fire(s, ModelEvent.ACTIVATE)


def test_deprecate_from_active():
    s = AIML_MODEL_FSM.fire(ModelState.ACTIVE, ModelEvent.DEPRECATE)
    assert s == ModelState.DEPRECATED


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
