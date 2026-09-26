import uuid

import pytest

from smo_sdk._common import SdkError
from smo_sdk.lifecycle import LifecycleClient


@pytest.fixture
def client(r1):
    return LifecycleClient(r1)


def test_request_training(client, r1):
    model_id = uuid.uuid4()
    client.request_training("producer-1", model_id=model_id, run_id="run-1")
    call = r1.calls[0]
    assert call["verb"] == "post"
    assert call["path"] == "/aimgf/training-jobs"
    assert call["json"]["modelId"] == str(model_id)
    assert call["json"]["producerId"] == "producer-1"
    assert call["json"]["runId"] == "run-1"
    assert call["json"]["modelCoordinationGroupId"] is None


def test_get_training_job_status(client, r1):
    job_id = uuid.uuid4()
    client.get_training_job_status(job_id)
    assert r1.calls[0] == {"verb": "get", "path": f"/aimgf/training-jobs/{job_id}/status", "params": None}


def test_cancel_training(client, r1):
    job_id = uuid.uuid4()
    client.cancel_training(job_id)
    assert r1.calls[0] == {"verb": "delete", "path": f"/aimgf/training-jobs/{job_id}", "params": None}


def test_update_training_job_model_metrics_sends_raw_unwrapped_body(client, r1):
    """model_metrics is the route's only body-eligible parameter — the
    wire body is that dict directly, not {"model_metrics": ...}.
    Confirmed against AIMgF's own OpenAPI schema before writing this."""
    job_id = uuid.uuid4()
    client.update_training_job_model_metrics(job_id, {"accuracy": 0.9})
    assert r1.calls[0] == {
        "verb": "post", "path": f"/aimgf/training-jobs/{job_id}/model-metrics",
        "params": None, "files": None, "json": {"accuracy": 0.9},
    }


def test_get_training_job_model_metrics(client, r1):
    job_id = uuid.uuid4()
    client.get_training_job_model_metrics(job_id)
    assert r1.calls[0] == {"verb": "get", "path": f"/aimgf/training-jobs/{job_id}/model-metrics", "params": None}


def test_list_training_jobs(client, r1):
    client.list_training_jobs(status="RUNNING")
    assert r1.calls[0] == {"verb": "get", "path": "/aimgf/training-jobs", "params": {"model_id": None, "status": "RUNNING"}}


def test_advance_model_lifecycle(client, r1):
    model_id = uuid.uuid4()
    client.advance_model_lifecycle(model_id, "CERTIFY")
    assert r1.calls[0] == {
        "verb": "post", "path": f"/aimgf/models/{model_id}/advance",
        "params": {"event": "CERTIFY"}, "files": None, "json": None,
    }


def test_request_inference(client, r1):
    model_id = uuid.uuid4()
    client.request_inference(model_id, notification_destination="http://x/notify")
    assert r1.calls[0] == {
        "verb": "post", "path": f"/aimgf/models/{model_id}/inference-jobs",
        "params": {"notification_destination": "http://x/notify"}, "files": None, "json": None,
    }


def test_get_inference_job_status(client, r1):
    job_id = uuid.uuid4()
    client.get_inference_job_status(job_id)
    assert r1.calls[0] == {"verb": "get", "path": f"/aimgf/inference-jobs/{job_id}/status", "params": None}


def test_resolve_inference(client, r1):
    job_id = uuid.uuid4()
    client.resolve_inference(job_id, True)
    assert r1.calls[0] == {
        "verb": "post", "path": f"/aimgf/inference-jobs/{job_id}/resolve",
        "params": {"succeeded": True}, "files": None, "json": None,
    }


def test_list_inference_jobs(client, r1):
    client.list_inference_jobs(model_id=None, status="RESOLVED")
    assert r1.calls[0] == {"verb": "get", "path": "/aimgf/inference-jobs", "params": {"model_id": None, "status": "RESOLVED"}}


def test_subscribe_performance_monitoring_embeds_body_under_two_params(client, r1):
    """Two body-eligible params (metric_types, guard_kpi_floor) → FastAPI
    embeds them under their own keys, unlike the single-param unwrapped
    cases elsewhere in this client."""
    model_id, dme_type_id = uuid.uuid4(), uuid.uuid4()
    client.subscribe_performance_monitoring(model_id, ["latency"], dme_type_id, guard_kpi_floor={"latency": 100})
    call = r1.calls[0]
    assert call["path"] == "/aimgf/mlmf/subscriptions"
    assert call["params"] == {"model_id": str(model_id), "dme_type_id": str(dme_type_id)}
    assert call["json"] == {"metric_types": ["latency"], "guard_kpi_floor": {"latency": 100}}


def test_report_performance_sends_raw_unwrapped_body(client, r1):
    sub_id = uuid.uuid4()
    client.report_performance(sub_id, {"latency": 50})
    assert r1.calls[0] == {
        "verb": "post", "path": f"/aimgf/mlmf/subscriptions/{sub_id}/reports",
        "params": None, "files": None, "json": {"latency": 50},
    }


def test_list_performance_subscriptions(client, r1):
    client.list_performance_subscriptions()
    assert r1.calls[0] == {"verb": "get", "path": "/aimgf/mlmf/subscriptions", "params": {"model_id": None}}


def test_list_performance_reports(client, r1):
    sub_id = uuid.uuid4()
    client.list_performance_reports(sub_id, limit=10)
    assert r1.calls[0] == {"verb": "get", "path": f"/aimgf/mlmf/subscriptions/{sub_id}/reports", "params": {"limit": 10}}


def test_list_recent_performance_reports(client, r1):
    client.list_recent_performance_reports(breached_only=True)
    assert r1.calls[0] == {
        "verb": "get", "path": "/aimgf/mlmf/reports",
        "params": {"breached_only": True, "limit": 50},
    }


def test_create_feature_group(client, r1):
    client.create_feature_group("fg-1", "feat1,feat2", "influx", "host", "8086", "bucket", "token", "org", "meas")
    call = r1.calls[0]
    assert call["path"] == "/aimgf/feature-groups"
    assert call["json"]["featureGroupName"] == "fg-1"
    assert call["json"]["enableDme"] is False


def test_list_feature_groups(client, r1):
    client.list_feature_groups()
    assert r1.calls[0] == {"verb": "get", "path": "/aimgf/feature-groups", "params": None}


def test_deploy_model_sends_raw_unwrapped_body(client, r1):
    """node_groups is the route's only body parameter (a plain list) —
    the wire body is that list directly, not {"node_groups": [...]}."""
    model_id = uuid.uuid4()
    client.deploy_model(model_id, ["ng-1", "ng-2"])
    assert r1.calls[0] == {
        "verb": "post", "path": f"/mllf/models/{model_id}/deploy",
        "params": None, "files": None, "json": ["ng-1", "ng-2"],
    }


def test_raises_sdk_error_on_a_4xx_response(client, r1):
    r1.script(409, {"detail": "invalid transition"})
    with pytest.raises(SdkError) as exc_info:
        client.advance_model_lifecycle(uuid.uuid4(), "CERTIFY")
    assert exc_info.value.status_code == 409
    assert exc_info.value.body == {"detail": "invalid transition"}
