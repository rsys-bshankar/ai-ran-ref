"""Tests of MLLF's one route, `POST /models/{id}/deploy`, and its health probe.

Run: `cd smo/mllf && PYTHONPATH=.:../shared python -m pytest tests -q`. No database: AIMgF is faked by patching
`app.main.R1Client.get` and `.patch` (see `_mock_lifecycle`), so the tests cover MLLF's gate and its call shape to AIMgF,
not AIMgF itself.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


class FakeResponse:
    """Stands in for an `httpx.Response`: only `status_code` and `json()` are used by the route."""
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def client():
    """A TestClient on the app; MLLF has no database to override."""
    return TestClient(app)


def _mock_lifecycle(monkeypatch, model_id, model_lifecycle_state, cleared_node_groups=None):
    """Patches `R1Client.get` to return a lifecycle row for `model_id` in the given state (404 for any other path) and `R1Client.patch`
    to store the sent `clearedNodeGroups` in that row. Returns the row so a test can inspect it.
    """
    lifecycle = {"modelId": str(model_id), "modelLifecycleState": model_lifecycle_state, "clearedNodeGroups": cleared_node_groups or []}

    def fake_get(self, path, **kw):
        return FakeResponse(200, lifecycle) if path == f"/aimgf/models/{model_id}/lifecycle" else FakeResponse(404, {})

    def fake_patch(self, path, json=None, **kw):
        lifecycle["clearedNodeGroups"] = json.get("clearedNodeGroups", lifecycle["clearedNodeGroups"])
        return FakeResponse(200, lifecycle)

    monkeypatch.setattr("app.main.R1Client.get", fake_get)
    monkeypatch.setattr("app.main.R1Client.patch", fake_patch)
    return lifecycle


def test_request_model_deployment_requires_certified_or_promoted(client, monkeypatch):
    """A model that is not CERTIFIED or PROMOTED is refused with 409 MODEL_NOT_CERTIFIED, and the answer names the state."""
    model_id = uuid.uuid4()
    _mock_lifecycle(monkeypatch, model_id, "TRAINING")
    resp = client.post(f"/models/{model_id}/deploy", json=["ng1"])
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "MODEL_NOT_CERTIFIED"
    assert "TRAINING" in resp.json()["detail"]["detail"]


def test_request_model_deployment_refuses_a_deprecated_model(client, monkeypatch):
    """A DEPRECATED model gets no new placement, so a model at end of life is not put on node groups."""
    model_id = uuid.uuid4()
    _mock_lifecycle(monkeypatch, model_id, "DEPRECATED")
    resp = client.post(f"/models/{model_id}/deploy", json=["ng1"])
    assert resp.status_code == 409
    assert "DEPRECATED" in resp.json()["detail"]["detail"]


def test_request_model_deployment_stamps_cleared_node_groups(client, monkeypatch):
    """A CERTIFIED model is accepted and the node groups sent are written to AIMgF and echoed back."""
    model_id = uuid.uuid4()
    _mock_lifecycle(monkeypatch, model_id, "CERTIFIED")
    resp = client.post(f"/models/{model_id}/deploy", json=["edge-gpu-a", "edge-gpu-b"])
    assert resp.status_code == 200
    assert resp.json() == {"modelId": str(model_id), "clearedNodeGroups": ["edge-gpu-a", "edge-gpu-b"]}


def test_request_model_deployment_also_allowed_once_promoted(client, monkeypatch):
    """PROMOTED passes the gate as well as CERTIFIED."""
    model_id = uuid.uuid4()
    _mock_lifecycle(monkeypatch, model_id, "PROMOTED")
    resp = client.post(f"/models/{model_id}/deploy", json=["ng1"])
    assert resp.status_code == 200


def test_request_model_deployment_for_unknown_model_is_404(client, monkeypatch):
    """When AIMgF has no lifecycle for the model, the route answers 404."""
    monkeypatch.setattr("app.main.R1Client.get", lambda self, path, **kw: FakeResponse(404, {}))
    resp = client.post(f"/models/{uuid.uuid4()}/deploy", json=["ng1"])
    assert resp.status_code == 404


def test_health_check_answers_the_gui_bff_liveness_probe(client):
    """`/health` answers 200 `{status: healthy}`, which the GUI BFF's module status probe relies on."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}
