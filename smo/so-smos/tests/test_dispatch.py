"""Tests for SO SMOS's execution semantics (SO SMOS LLD section 1.1):
sequential, fail-fast, no auto-compensation. Run with:
pytest smo/so-smos/tests -q
"""

from unittest.mock import MagicMock

from app.dispatch import execute_order


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
