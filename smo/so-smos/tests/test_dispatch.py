"""Tests for SO SMOS's execution semantics (SO SMOS LLD section 1.1):
sequential, fail-fast, no auto-compensation. Run with:
pytest smo/so-smos/tests -q
"""

from unittest.mock import MagicMock

import pytest

from app.dispatch import (
    DownstreamError, _ensure_ok, dispatch_config, dispatch_deploy,
    dispatch_infra, dispatch_policy, dispatch_training, execute_order,
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
        {"stepType": "POLICY", "targetModule": "A1_RELATED", "policyTypeId": "t1", "policyObject": {}, "nearRtRicId": "ric1"},
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
    resp.json.return_value = {"title": "POLICY_TYPE_NOT_SUPPORTED"}
    with pytest.raises(DownstreamError):
        _ensure_ok(resp)


@pytest.mark.parametrize("dispatcher,step,expected_path", [
    (dispatch_config, {"scope": "cell-1", "changes": []}, "/ran-nf-oam/config-jobs"),
    (dispatch_deploy, {"nfDeploymentDescriptorId": "d1"}, "/nfo/deployments"),
    (dispatch_infra, {"spec": {"cpu": 4}}, "/focom/resources/provision"),
    (dispatch_training, {"modelId": "m1"}, "/ai-ml-workflow/training-jobs"),
    (dispatch_policy, {"policyTypeId": "t1", "policyObject": {}, "nearRtRicId": "ric1"}, "/a1-related/policies"),
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
