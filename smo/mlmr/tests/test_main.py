"""Tests for MLMR's routes (AI/ML Workflow LLD sections 1, 4).
Run with: pytest smo/mlmr/tests -q
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from smo_shared.db import Base, get_session
from smo_shared.testing import make_test_engine

from app.main import app
from app.models import MLModel, MLModelCoordinationGroup, MLModelRepository, ModelArtifact, MLModelProfile, MLModelsStorage


@pytest.fixture
def db_session_factory():
    # make_test_engine(), not a plain create_engine("sqlite://", ...) —
    # MLModelCoordinationGroup.member_model_ids is an ARRAY(Uuid), whose
    # SQLite JSON fallback needs the UUID-aware serializer make_test_engine
    # provides (see smo_shared/testing.py's own docstring).
    engine = make_test_engine()
    Base.metadata.create_all(engine, tables=[
        MLModelRepository.__table__, MLModel.__table__, MLModelCoordinationGroup.__table__, ModelArtifact.__table__,
        MLModelsStorage.__table__, MLModelProfile.__table__,
    ])
    return sessionmaker(bind=engine)


@pytest.fixture
def client(db_session_factory):
    def override_get_session():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _make_model(db_session_factory, model_type="t") -> uuid.UUID:
    model_id = uuid.uuid4()
    with db_session_factory() as session:
        session.add(MLModel(model_id=model_id, registration_id=str(uuid.uuid4()), model_type=model_type, version="1.0"))
        session.commit()
    return model_id


def test_get_model_by_id_returns_its_fields(client):
    """HISTORY.md §5: model CRUD was incomplete — only create
    and a type-filtered list existed, no GET-by-id at all.
    """
    created = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"}).json()

    resp = client.get(f"/models/{created['modelId']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["modelId"] == created["modelId"]
    assert body["modelType"] == "coverage-predictor"
    assert body["version"] == "1.0"


def test_register_model_stores_and_exposes_registration_metadata(client):
    """HISTORY.md §5: registration metadata was thin — no I/O
    data type schema, no author/owner, no TargetEnvironment
    declarations, all real fields on the reference's own
    ModelRelatedInformation/ModelInformation/Metadata (modelInfo.go).
    """
    target_environments = [{"platformName": "k8s-cluster-1", "environmentType": "PRODUCTION", "dependencyList": "numpy==1.26"}]
    resp = client.post("/models", json={
        "modelType": "coverage-predictor", "version": "1.0", "description": "predicts coverage gaps",
        "author": "team-ran", "owner": "team-ran-oncall", "inputDataType": "csv", "outputDataType": "json",
        "targetEnvironments": target_environments,
    })
    model_id = resp.json()["modelId"]

    view = client.get(f"/models/{model_id}").json()
    assert view["description"] == "predicts coverage gaps"
    assert view["author"] == "team-ran"
    assert view["owner"] == "team-ran-oncall"
    assert view["inputDataType"] == "csv"
    assert view["outputDataType"] == "json"
    assert view["targetEnvironments"] == target_environments


def test_register_model_without_metadata_defaults_to_empty(client):
    """This build's own RegisterModel stays permissive — none of the new
    fields are required, unlike the reference's own validate:"required".
    """
    model_id = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"}).json()["modelId"]

    view = client.get(f"/models/{model_id}").json()
    assert view["description"] is None
    assert view["author"] is None
    assert view["targetEnvironments"] == []


def test_update_model_changes_registration_metadata(client):
    model_id = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"}).json()["modelId"]

    resp = client.put(f"/models/{model_id}", json={
        "modelType": "coverage-predictor", "version": "1.0", "description": "updated description",
        "author": "team-ran", "targetEnvironments": [{"platformName": "k8s-cluster-2", "environmentType": "STAGING", "dependencyList": ""}],
    })
    assert resp.status_code == 200
    assert resp.json()["description"] == "updated description"
    assert resp.json()["author"] == "team-ran"
    assert resp.json()["targetEnvironments"][0]["platformName"] == "k8s-cluster-2"


def test_register_model_rejects_duplicate_type_and_version(client):
    """HISTORY.md §5: the reference's own RegisterModel
    (mmes_apis.go) 409s on a (modelName, modelVersion) unique-constraint
    violation — this build accepted a duplicate silently, creating a
    second, indistinguishable row for the same (modelType, version).
    """
    first = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"})
    assert first.status_code == 201

    resp = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "MODEL_ALREADY_REGISTERED"

    all_models = client.get("/models").json()["items"]
    assert len(all_models) == 1


def test_register_model_allows_a_different_version_of_the_same_type(client):
    client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"})
    resp = client.post("/models", json={"modelType": "coverage-predictor", "version": "2.0"})
    assert resp.status_code == 201


def test_register_model_allows_the_same_version_of_a_different_type(client):
    client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"})
    resp = client.post("/models", json={"modelType": "throughput-predictor", "version": "1.0"})
    assert resp.status_code == 201


def test_get_unknown_model_is_404(client):
    resp = client.get(f"/models/{uuid.uuid4()}")
    assert resp.status_code == 404


# ---------------------------------------------------------------- Wave 3: TS29482_MLR_MLModelManagement.yaml

def test_register_model_rejects_unknown_domain(client):
    resp = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0", "domain": "TELEPATHY"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "SCHEMA_VALIDATION_FAILED"


def test_register_model_stores_and_exposes_domain_and_vendors(client):
    model_id = client.post("/models", json={
        "modelType": "coverage-predictor", "version": "1.0", "domain": "CUSTOM",
        "customDomain": "coverage-optimization", "vendors": ["acme", "globex"],
    }).json()["modelId"]

    view = client.get(f"/models/{model_id}").json()
    assert view["domain"] == "CUSTOM"
    assert view["customDomain"] == "coverage-optimization"
    assert view["vendors"] == ["acme", "globex"]


def test_register_model_without_domain_defaults_to_empty(client):
    model_id = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"}).json()["modelId"]
    view = client.get(f"/models/{model_id}").json()
    assert view["domain"] is None
    assert view["customDomain"] is None
    assert view["vendors"] == []


def test_update_model_rejects_unknown_domain(client):
    model_id = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"}).json()["modelId"]
    resp = client.put(f"/models/{model_id}", json={"modelType": "coverage-predictor", "version": "1.0", "domain": "TELEPATHY"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "SCHEMA_VALIDATION_FAILED"


def test_update_model_changes_domain_and_vendors(client):
    model_id = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"}).json()["modelId"]
    resp = client.put(f"/models/{model_id}", json={
        "modelType": "coverage-predictor", "version": "1.0", "domain": "IMAGE_RECOGNITION", "vendors": ["acme"],
    })
    assert resp.status_code == 200
    assert resp.json()["domain"] == "IMAGE_RECOGNITION"
    assert resp.json()["vendors"] == ["acme"]


def test_upload_model_artifact_records_size_bytes(client):
    model_id = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"}).json()["modelId"]
    resp = client.post(f"/models/{model_id}/artifact", files={"file": ("model.zip", b"twelve-bytes", "application/zip")})
    assert resp.status_code == 201
    assert resp.json()["sizeBytes"] == len(b"twelve-bytes")


def test_upload_model_artifact_stamps_version_one_and_records_location(client):
    """HISTORY.md §5: the reference's real UploadModel — ours had
    an artifact_location field nothing in main.py ever read or wrote.
    """
    model_id = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"}).json()["modelId"]

    resp = client.post(f"/models/{model_id}/artifact", files={"file": ("model.zip", b"pkzip-bytes", "application/zip")})
    assert resp.status_code == 201
    body = resp.json()
    assert body["modelId"] == model_id
    assert body["artifactVersion"] == 1

    view = client.get(f"/models/{model_id}").json()
    assert view["artifactLocation"] == f"model-artifact:{model_id}:1"


def test_upload_model_artifact_versions_increment_independently_of_model_version(client):
    """artifactVersion is a separate auto-incrementing counter from
    modelVersion — a second upload against the same model bumps it to 2
    without touching MLModel.version at all.
    """
    model_id = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"}).json()["modelId"]
    client.post(f"/models/{model_id}/artifact", files={"file": ("model.zip", b"first", "application/zip")})

    second = client.post(f"/models/{model_id}/artifact", files={"file": ("model.zip", b"second", "application/zip")})
    assert second.json()["artifactVersion"] == 2

    unchanged = client.get(f"/models/{model_id}").json()
    assert unchanged["version"] == "1.0"


def test_upload_model_artifact_rejects_non_zip(client):
    """The reference's own UploadModel validation: anything but a .zip
    suffix is 415 Unsupported Media Type.
    """
    model_id = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"}).json()["modelId"]

    resp = client.post(f"/models/{model_id}/artifact", files={"file": ("model.tar", b"not-a-zip", "application/x-tar")})
    assert resp.status_code == 415


def test_upload_model_artifact_for_unknown_model_is_404(client):
    resp = client.post(f"/models/{uuid.uuid4()}/artifact", files={"file": ("model.zip", b"bytes", "application/zip")})
    assert resp.status_code == 404


def test_download_model_artifact_round_trips_the_uploaded_bytes(client):
    """DownloadModel — byte-for-byte round trip against the same
    modelId+artifactVersion key UploadModel stamped."""
    model_id = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"}).json()["modelId"]
    client.post(f"/models/{model_id}/artifact", files={"file": ("model.zip", b"pkzip-bytes", "application/zip")})

    resp = client.get(f"/models/{model_id}/artifact/1")
    assert resp.status_code == 200
    assert resp.content == b"pkzip-bytes"
    assert resp.headers["content-type"] == "application/zip"


def test_download_unknown_artifact_version_is_404(client):
    model_id = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"}).json()["modelId"]
    resp = client.get(f"/models/{model_id}/artifact/1")
    assert resp.status_code == 404


def test_update_model_changes_metadata_fields(client):
    """HISTORY.md §5: model CRUD was incomplete — create, list,
    and (as of the previous pass) get-by-id existed, but no update at all.
    requiredResourceTypeId/trainingDataLineage/integrityHash are write-only
    from `_model_view`'s own perspective (pre-existing, unrelated to Wave
    2's clearedNodeGroups removal) — this only asserts the write itself
    succeeds, via description/author, which the view does expose.
    """
    model_id = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0", "requiredResourceTypeId": "gpu-a"}).json()["modelId"]

    resp = client.put(f"/models/{model_id}", json={
        "modelType": "coverage-predictor", "version": "1.0", "requiredResourceTypeId": "gpu-b",
        "trainingDataLineage": {"source": "dme-type-1"}, "integrityHash": "sha256:abc",
        "description": "updated via metadata test",
    })
    assert resp.status_code == 200
    assert resp.json()["description"] == "updated via metadata test"

    view = client.get(f"/models/{model_id}").json()
    assert view["description"] == "updated via metadata test"


def test_update_model_rejects_changing_its_identity(client):
    """UpdateModel (mmes_apis.go) 400s when the body's modelName/
    modelVersion doesn't match the existing record at that id — identity
    is immutable, matching register_model's own (model_type, version)
    uniqueness constraint.
    """
    model_id = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"}).json()["modelId"]

    resp = client.put(f"/models/{model_id}", json={"modelType": "coverage-predictor", "version": "2.0"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["title"] == "MODEL_IDENTITY_IMMUTABLE"

    unchanged = client.get(f"/models/{model_id}").json()
    assert unchanged["version"] == "1.0"


def test_update_unknown_model_is_404(client):
    resp = client.put(f"/models/{uuid.uuid4()}", json={"modelType": "t", "version": "1.0"})
    assert resp.status_code == 404


def test_delete_model_removes_it(client):
    model_id = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"}).json()["modelId"]

    resp = client.delete(f"/models/{model_id}")
    assert resp.status_code == 204
    assert client.get(f"/models/{model_id}").status_code == 404


def test_delete_unknown_model_is_idempotent(client):
    resp = client.delete(f"/models/{uuid.uuid4()}")
    assert resp.status_code == 204


def test_delete_model_cascades_its_own_artifacts(client):
    """HISTORY.md §5: model_artifact had no cascade behavior at
    all. Wave 1's split moved TrainingJob/ModelChangeSubscription/
    MLMFSubscription/InferenceJob/PerformanceReport to AIMgF's own
    process — this module no longer imports them, so it can no longer
    assert their cleanup directly. That cleanup is still real: every one
    of those FKs still carries `ON DELETE CASCADE` in
    migrations/001_init.sql, unaffected by the code split, and every
    module shares one physical Postgres instance — but it's authoritative
    at the database level, not exercised by this SQLite-backed unit test
    (SQLite doesn't enforce FK constraints by default, and this module's
    own isolated test schema doesn't even declare AIMgF's tables).
    Verified separately against a live Postgres instance instead.
    """
    model_id = uuid.UUID(client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"}).json()["modelId"])
    client.post(f"/models/{model_id}/artifact", files={"file": ("model.zip", b"bytes", "application/zip")})

    resp = client.delete(f"/models/{model_id}")
    assert resp.status_code == 204
    assert client.get(f"/models/{model_id}/artifact/1").status_code == 404


def test_list_coordination_groups_returns_members(client, db_session_factory):
    model_id_1 = _make_model(db_session_factory, model_type="t1")
    model_id_2 = _make_model(db_session_factory, model_type="t2")
    group_id = client.post("/coordination-groups", json={"memberModelIds": [str(model_id_1), str(model_id_2)]}).json()["groupId"]

    groups = client.get("/coordination-groups").json()["items"]
    assert [(g["groupId"], g["memberModelIds"]) for g in groups] == [(group_id, [str(model_id_1), str(model_id_2)])]


def test_create_coordination_group_rejects_fewer_than_two_members(client, db_session_factory):
    """The migration's own CHECK constraint (array_length >= 2) enforces
    this at the DB layer, but SQLite's test schema (built from the ORM
    models, which never mirrored the constraint) doesn't — so only a
    real-Postgres run ever caught the unhandled IntegrityError this used
    to raise. Pre-validated here now instead.
    """
    model_id = _make_model(db_session_factory)
    resp = client.post("/coordination-groups", json={"memberModelIds": [str(model_id)]})
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "COORDINATION_GROUP_TOO_SMALL"

    resp = client.post("/coordination-groups", json={"memberModelIds": []})
    assert resp.status_code == 422


def test_health_check_answers_the_gui_bff_liveness_probe(client):
    """GUI pass: the BFF's /modules/status probes /<module>/health on every module."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


# ---------------------------------------------------------------- Wave 4: TS 28.105 MLModel / MLModelRepository / MLModelCoordinationGroup

def _fake_aimgf_refs(monkeypatch, payload=None, status=200):
    class Resp:
        status_code = status

        def json(self):
            return payload

    calls = []

    def fake_get(self, path, **kw):
        calls.append(path)
        return Resp()

    monkeypatch.setattr("app.main.R1Client.get", fake_get)
    return calls


def test_register_with_ts28105_attributes_and_nrm_view(client, monkeypatch):
    repo = client.post("/ml-model-repositories", json={"userLabel": "mlmr-1"}).json()
    resp = client.post("/models", json={
        "modelType": "energy-saving", "version": "1.0", "aIMLInferenceName": "NG_RAN_NETWORK_ENERGY_SAVING",
        "trainingContext": {"dataProviderRef": ["PRB_UTILIZATION"]},
        "supportedPerformanceIndicators": [{"performanceIndicatorName": "MAE", "isSupportedForTraining": True}],
        "mLCapabilitiesInfoList": [{"capabilityName": "sleep-recommendation"}],
        "inferenceScope": ["NG_RAN_NETWORK_ENERGY_SAVING"], "mLModelRepositoryRef": repo["id"],
    })
    model_id = resp.json()["modelId"]
    calls = _fake_aimgf_refs(monkeypatch, {"mLTrainingType": "INITIAL_TRAINING", "aIMLInferenceReportRefList": ["r1"],
                                           "usedByFunctionRefList": ["f1"]})
    attrs = client.get(f"/ml-models/{model_id}").json()["attributes"]
    assert calls == [f"/aimgf/ml-models/{model_id}/nrm-refs"]
    assert attrs["mLModelId"] == model_id and attrs["mLModelVersion"] == "1.0"
    assert attrs["aIMLInferenceName"] == "NG_RAN_NETWORK_ENERGY_SAVING"
    assert attrs["trainingContext"] == {"dataProviderRef": ["PRB_UTILIZATION"]}
    assert attrs["supportedPerformanceIndicators"][0]["isSupportedForTesting"] is False
    assert (attrs["mLTrainingType"], attrs["usedByFunctionRefList"]) == ("INITIAL_TRAINING", ["f1"])
    assert client.get(f"/ml-model-repositories/{repo['id']}").json()["MLModel"] == [model_id]
    # this build's own view carries the writable spec attributes too
    assert client.get(f"/models/{model_id}").json()["mLModelRepositoryRef"] == repo["id"]


def test_nrm_view_degrades_when_aimgf_unreachable(client, monkeypatch):
    model_id = client.post("/models", json={"modelType": "m", "version": "1"}).json()["modelId"]
    _fake_aimgf_refs(monkeypatch, None, status=503)
    attrs = client.get(f"/ml-models/{model_id}").json()["attributes"]
    assert attrs["mLTrainingType"] is None and attrs["aIMLInferenceReportRefList"] == []


def test_ts28105_attributes_are_validated(client):
    assert client.post("/models", json={"modelType": "m", "version": "1",
                                        "supportedPerformanceIndicators": []}).status_code == 422
    assert client.post("/models", json={"modelType": "m", "version": "1",
                                        "trainingContext": {"notInSpec": 1}}).status_code == 422
    assert client.post("/models", json={"modelType": "m", "version": "1",
                                        "mLModelRepositoryRef": str(uuid.uuid4())}).status_code == 404
    assert client.post("/models", json={"modelType": "m", "version": "1",
                                        "sourceTrainedMLModelRef": str(uuid.uuid4())}).status_code == 404


def test_source_trained_model_ref_and_update(client):
    base = client.post("/models", json={"modelType": "m", "version": "1"}).json()["modelId"]
    derived = client.post("/models", json={"modelType": "m", "version": "2", "sourceTrainedMLModelRef": base}).json()["modelId"]
    assert client.get(f"/models/{derived}").json()["sourceTrainedMLModelRef"] == base
    resp = client.put(f"/models/{derived}", json={"modelType": "m", "version": "2", "aIMLInferenceName": "X"})
    assert resp.json()["aIMLInferenceName"] == "X"


def test_coordination_group_nrm_view_and_repository_delete_uncontains(client):
    repo = client.post("/ml-model-repositories", json={}).json()
    a = client.post("/models", json={"modelType": "a", "version": "1", "mLModelRepositoryRef": repo["id"]}).json()["modelId"]
    b = client.post("/models", json={"modelType": "b", "version": "1"}).json()["modelId"]
    group_id = client.post("/coordination-groups", json={"memberModelIds": [a, b], "mLModelRepositoryRef": repo["id"]}).json()["groupId"]
    view = client.get(f"/ml-model-coordination-groups/{group_id}").json()
    assert view["attributes"]["memberMLModelRefList"] == [a, b]
    assert client.get(f"/ml-model-repositories/{repo['id']}").json()["MLModelCoordinationGroup"] == [group_id]
    assert client.delete(f"/ml-model-repositories/{repo['id']}").status_code == 204
    assert client.get(f"/models/{a}").json()["mLModelRepositoryRef"] is None
    assert client.get(f"/ml-model-coordination-groups/{group_id}").json()["attributes"]["mLModelRepositoryRef"] is None
    assert client.get(f"/ml-model-repositories/{repo['id']}").status_code == 404
