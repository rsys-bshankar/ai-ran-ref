"""Tests for MLLF's routes (AI/ML Workflow LLD section 5).
Run with: pytest smo/mllf/tests -q

Deliberately thin this wave (see app/main.py's own docstring): the one
existing route (`request_model_deployment`) reads/writes MLMR's model
row through R1Client, faked here the same "monkeypatch
app.main.R1Client.<verb>" shape this build already uses for e.g.
nfo/tests/test_main.py's own FOCOM double.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def client():
    return TestClient(app)


def _mock_model(monkeypatch, model_id, state, cleared_node_groups=None):
    model = {"modelId": str(model_id), "state": state, "clearedNodeGroups": cleared_node_groups or []}

    def fake_get(self, path, **kw):
        return FakeResponse(200, model) if path == f"/mlmr/models/{model_id}" else FakeResponse(404, {})

    def fake_patch(self, path, json=None, **kw):
        model["clearedNodeGroups"] = json.get("clearedNodeGroups", model["clearedNodeGroups"])
        return FakeResponse(200, model)

    monkeypatch.setattr("app.main.R1Client.get", fake_get)
    monkeypatch.setattr("app.main.R1Client.patch", fake_patch)
    return model


def test_request_model_deployment_requires_certified_or_later(client, monkeypatch):
    model_id = uuid.uuid4()
    _mock_model(monkeypatch, model_id, "TRAINING")
    resp = client.post(f"/models/{model_id}/deploy", json=["ng1"])
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "MODEL_NOT_CERTIFIED"


def test_request_model_deployment_stamps_cleared_node_groups(client, monkeypatch):
    """RequestModelDeployment — triggers MLLF. Requires CERTIFIED-or-later
    state (the AIMgF gate, unchanged from v1.3) and stamps
    clearedNodeGroups (LLD section 5, MultiNode Q2's targeting gap).
    """
    model_id = uuid.uuid4()
    _mock_model(monkeypatch, model_id, "CERTIFIED")
    resp = client.post(f"/models/{model_id}/deploy", json=["edge-gpu-a", "edge-gpu-b"])
    assert resp.status_code == 200
    assert resp.json() == {"modelId": str(model_id), "clearedNodeGroups": ["edge-gpu-a", "edge-gpu-b"]}


def test_request_model_deployment_for_unknown_model_is_404(client, monkeypatch):
    monkeypatch.setattr("app.main.R1Client.get", lambda self, path, **kw: FakeResponse(404, {}))
    resp = client.post(f"/models/{uuid.uuid4()}/deploy", json=["ng1"])
    assert resp.status_code == 404


def test_health_check_answers_the_gui_bff_liveness_probe(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}
