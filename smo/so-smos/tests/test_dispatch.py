"""Tests for SO SMOS's execution semantics (SO SMOS LLD section 1.1):
sequential, fail-fast, no auto-compensation. Run with:
pytest smo/so-smos/tests -q
"""

from unittest.mock import MagicMock

import pytest

from app.dispatch import (
    DownstreamError, _ensure_ok, dispatch_config, dispatch_deploy,
    dispatch_emulation, dispatch_inference, dispatch_infra,
    dispatch_model_runtime_deploy, dispatch_training,
    dispatch_validation, execute_order,
)


def _ok_response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.status_code = 200
    return resp


def test_all_steps_succeed_in_order():
    r1 = MagicMock()
    r1.post.return_value = _ok_response({"jobId": "abc"})

    steps = [
        {"stepType": "CONFIG", "targetModule": "RAN_NF_OAM", "scope": "cell", "changes": []},
        {"stepType": "DEPLOY", "targetModule": "NFO", "nfDeploymentDescriptorId": "d1"},
    ]
    results = execute_order(r1, steps)

    assert [s["status"] for s in results] == ["COMPLETED", "COMPLETED"]
    assert r1.post.call_count == 2


def test_first_failure_halts_remaining_steps_as_pending():
    """The core decision: fail-fast, no compensation. Steps after the
    failure never execute — they stay PENDING, not SKIPPED or FAILED.
    """
    r1 = MagicMock()
    r1.post.side_effect = [Exception("endpoint unreachable"), _ok_response({})]

    steps = [
        {"stepType": "CONFIG", "targetModule": "RAN_NF_OAM", "scope": "cell", "changes": []},
        {"stepType": "DEPLOY", "targetModule": "NFO", "nfDeploymentDescriptorId": "d1"},
    ]
    results = execute_order(r1, steps)

    assert results[0]["status"] == "FAILED"
    assert results[1]["status"] == "PENDING"
    # the second step's dispatcher was never actually called
    assert r1.post.call_count == 1


def test_unknown_step_target_pair_fails_without_crashing():
    r1 = MagicMock()
    steps = [{"stepType": "CONFIG", "targetModule": "SOME_UNMAPPED_MODULE"}]
    results = execute_order(r1, steps)
    assert results[0]["status"] == "FAILED"
    assert "no dispatcher" in results[0]["error"]


def test_ensure_ok_passes_through_a_successful_response():
    assert _ensure_ok(_ok_response({"jobId": "abc"})) == {"jobId": "abc"}


def test_ensure_ok_raises_downstream_error_on_4xx():
    """The actual fix SO SMOS LLD section 1.1 needed: a non-2xx downstream
    response must halt the order, not get recorded as COMPLETED.
    """
    resp = MagicMock()
    resp.status_code = 422
    resp.json.return_value = {"title": "UNPROCESSABLE"}
    with pytest.raises(DownstreamError):
        _ensure_ok(resp)


@pytest.mark.parametrize("dispatcher,step,expected_path", [
    (dispatch_config, {"scope": "cell-1", "changes": []}, "/ran-nf-oam/config-jobs"),
    (dispatch_deploy, {"nfDeploymentDescriptorId": "d1"}, "/nfo/deployments"),
    (dispatch_infra, {"spec": {"cpu": 4}}, "/focom/resources/provision"),
    (dispatch_training, {"modelId": "m1"}, "/aimgf/training-jobs"),
    (dispatch_validation, {"modelId": "m1"}, "/aimgf/validation-jobs"),
    (dispatch_emulation, {"modelId": "m1"}, "/aimgf/emulation-jobs"),
    (dispatch_model_runtime_deploy, {"modelId": "m1"}, "/aimgf/models/m1/runtime/deploy"),
    (dispatch_inference, {"modelId": "m1"}, "/aimgf/models/m1/inference-jobs"),
])
def test_each_dispatcher_posts_to_its_own_target_module(dispatcher, step, expected_path):
    """SO SMOS LLD section 1's dispatch table, one entry at a time: each
    stepType x targetModule pair must hit the exact module its LLD
    section names — a typo here would silently route a step to the wrong
    service.
    """
    r1 = MagicMock()
    r1.post.return_value = _ok_response({"ok": True})
    dispatcher(r1, step)
    assert r1.post.call_args.args[0] == expected_path


def test_dispatch_inference_forwards_notification_destination_as_a_query_param():
    """AIMgF's own RequestInference (call flow 02) takes
    notification_destination as a query param, not a JSON body field —
    mirroring that shape exactly rather than silently dropping it.
    """
    r1 = MagicMock()
    r1.post.return_value = _ok_response({"inferenceJobId": "i1"})
    dispatch_inference(r1, {"modelId": "m1", "notificationDestination": "http://consumer/cb"})
    assert r1.post.call_args.kwargs["params"] == {"notification_destination": "http://consumer/cb"}


def test_dispatch_inference_without_notification_destination_sends_no_params():
    r1 = MagicMock()
    r1.post.return_value = _ok_response({"inferenceJobId": "i1"})
    dispatch_inference(r1, {"modelId": "m1"})
    assert r1.post.call_args.kwargs["params"] is None


def test_full_ai_ml_pipeline_can_now_be_composed_in_one_order():
    """HISTORY.md OI-6.6, closed: previously only TRAINING had a
    dispatch entry — an operator could not compose Validation, Emulation,
    a model-runtime Deploy, or Inference into a multi-step ServiceOrder
    the way call flow 10 already shows for Training. All five now dispatch.
    """
    r1 = MagicMock()
    r1.post.return_value = _ok_response({"ok": True})

    steps = [
        {"stepType": "TRAINING", "targetModule": "AI_ML_WORKFLOW", "modelId": "m1"},
        {"stepType": "VALIDATION", "targetModule": "AI_ML_WORKFLOW", "modelId": "m1"},
        {"stepType": "EMULATION", "targetModule": "AI_ML_WORKFLOW", "modelId": "m1"},
        {"stepType": "DEPLOY", "targetModule": "AIMGF", "modelId": "m1"},
        {"stepType": "INFERENCE", "targetModule": "AI_ML_WORKFLOW", "modelId": "m1"},
    ]
    results = execute_order(r1, steps)

    assert [s["status"] for s in results] == ["COMPLETED"] * 5
    assert [c.args[0] for c in r1.post.call_args_list] == [
        "/aimgf/training-jobs", "/aimgf/validation-jobs", "/aimgf/emulation-jobs",
        "/aimgf/models/m1/runtime/deploy", "/aimgf/models/m1/inference-jobs",
    ]


def test_model_runtime_deploy_and_workload_deploy_are_distinct_dispatch_entries():
    """(\"DEPLOY\", \"NFO\") and (\"DEPLOY\", \"AIMGF\") share a stepType but
    route to entirely different services — same key collision risk this
    table already avoids everywhere else (distinct (stepType,
    targetModule) pairs), just now exercised for DEPLOY specifically.
    """
    r1 = MagicMock()
    r1.post.return_value = _ok_response({"ok": True})

    steps = [
        {"stepType": "DEPLOY", "targetModule": "NFO", "nfDeploymentDescriptorId": "d1"},
        {"stepType": "DEPLOY", "targetModule": "AIMGF", "modelId": "m1"},
    ]
    results = execute_order(r1, steps)

    assert [s["status"] for s in results] == ["COMPLETED", "COMPLETED"]
    assert [c.args[0] for c in r1.post.call_args_list] == ["/nfo/deployments", "/aimgf/models/m1/runtime/deploy"]
