import uuid

import pytest

from smo_sdk._common import SdkError
from smo_sdk.models import ModelsClient


@pytest.fixture
def client(r1):
    return ModelsClient(r1)


def test_register_model(client, r1):
    client.register_model("QoE-predictor", "1.0", description="d", author="a", owner="o")
    call = r1.calls[0]
    assert call["verb"] == "post"
    assert call["path"] == "/mlmr/models"
    assert call["json"] == {
        "modelType": "QoE-predictor", "version": "1.0", "requiredResourceTypeId": None,
        "description": "d", "author": "a", "owner": "o", "inputDataType": None,
        "outputDataType": None, "targetEnvironments": [],
    }


def test_discover_models(client, r1):
    client.discover_models(model_type="QoE-predictor")
    assert r1.calls[0] == {"verb": "get", "path": "/mlmr/models", "params": {"model_type": "QoE-predictor"}}


def test_get_model(client, r1):
    model_id = uuid.uuid4()
    client.get_model(model_id)
    assert r1.calls[0] == {"verb": "get", "path": f"/mlmr/models/{model_id}", "params": None}


def test_update_model(client, r1):
    model_id = uuid.uuid4()
    client.update_model(model_id, "QoE-predictor", "1.0", description="new desc")
    call = r1.calls[0]
    assert call["verb"] == "put"
    assert call["path"] == f"/mlmr/models/{model_id}"
    assert call["json"] == {"modelType": "QoE-predictor", "version": "1.0", "description": "new desc"}


def test_deregister_model(client, r1):
    model_id = uuid.uuid4()
    client.deregister_model(model_id)
    assert r1.calls[0] == {"verb": "delete", "path": f"/mlmr/models/{model_id}", "params": None}


def test_upload_artifact(client, r1):
    model_id = uuid.uuid4()
    client.upload_artifact(model_id, "model.zip", b"binarydata")
    call = r1.calls[0]
    assert call["verb"] == "post"
    assert call["path"] == f"/mlmr/models/{model_id}/artifact"
    assert call["json"] is None
    assert call["files"] == {"file": ("model.zip", b"binarydata", "application/zip")}


def test_download_artifact_returns_raw_response(client, r1):
    model_id = uuid.uuid4()
    r1.script(200, content=b"zipbytes")
    resp = client.download_artifact(model_id, 1)
    assert r1.calls[0] == {"verb": "get", "path": f"/mlmr/models/{model_id}/artifact/1", "params": None}
    assert resp.content == b"zipbytes"


def test_download_artifact_raises_sdk_error_on_4xx(client, r1):
    model_id = uuid.uuid4()
    r1.script(404, text="not found")
    with pytest.raises(SdkError) as exc_info:
        client.download_artifact(model_id, 99)
    assert exc_info.value.status_code == 404
    assert exc_info.value.body == "not found"


def test_create_coordination_group(client, r1):
    m1, m2 = uuid.uuid4(), uuid.uuid4()
    client.create_coordination_group([m1, m2], member_use_cases=["retrain-together"])
    call = r1.calls[0]
    assert call["path"] == "/mlmr/coordination-groups"
    assert call["json"] == {
        "memberModelIds": [str(m1), str(m2)], "memberUseCases": ["retrain-together"],
        "sharedFeaturePipelineRef": None, "retrainPropagation": "ANY_MEMBER_TRIGGERS",
    }


def test_list_coordination_groups(client, r1):
    client.list_coordination_groups()
    assert r1.calls[0] == {"verb": "get", "path": "/mlmr/coordination-groups", "params": None}
