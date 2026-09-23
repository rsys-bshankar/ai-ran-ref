"""Tests for AI/ML Workflow's routes (AI/ML Workflow LLD sections 1, 4)
— RequestTraining's model-state-to-event mapping and its handling of an
already-in-flight TrainingJob, neither of which had route-level coverage
before (only the FSM itself, in test_statemachine.py).
Run with: pytest smo/ai-ml-workflow/tests -q
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from smo_shared.db import Base, get_session
from smo_shared.testing import make_test_engine

from app.main import app
from app.models import AIMLModel, InferenceJob, MLMFSubscription, MLModelCoordinationGroup, ModelArtifact, ModelChangeSubscription, PerformanceReport, TrainingJob
from app.statemachine import ModelState


@pytest.fixture
def db_session_factory():
    # make_test_engine(), not a plain create_engine("sqlite://", ...) —
    # MLModelCoordinationGroup.member_model_ids is an ARRAY(Uuid), whose
    # SQLite JSON fallback needs the UUID-aware serializer make_test_engine
    # provides (see smo_shared/testing.py's own docstring).
    engine = make_test_engine()
    Base.metadata.create_all(engine, tables=[
        AIMLModel.__table__, TrainingJob.__table__, MLModelCoordinationGroup.__table__,
        MLMFSubscription.__table__, PerformanceReport.__table__, ModelArtifact.__table__,
        ModelChangeSubscription.__table__, InferenceJob.__table__,
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


def _make_model(db_session_factory, state) -> uuid.UUID:
    model_id = uuid.uuid4()
    with db_session_factory() as session:
        session.add(AIMLModel(model_id=model_id, registration_id=str(uuid.uuid4()), model_type="t", version="1.0", state=state))
        session.commit()
    return model_id


def test_request_training_on_registered_model_fires_train(client, db_session_factory):
    model_id = _make_model(db_session_factory, ModelState.REGISTERED)
    resp = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"})
    assert resp.status_code == 201

    with db_session_factory() as session:
        model = session.get(AIMLModel, model_id)
        assert model.state == ModelState.TRAINING


def test_request_training_on_active_model_fires_retrain_not_train(client, db_session_factory):
    """The actual bug this pass fixed: RequestTraining always fired TRAIN
    regardless of the model's state, which is only a legal transition
    from REGISTERED — every real retrain attempt (the normal case, once
    a model has ever reached ACTIVE) crashed with an unhandled
    IllegalTransition instead of re-entering at TRAINING via RETRAIN.
    """
    model_id = _make_model(db_session_factory, ModelState.ACTIVE)
    resp = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"})
    assert resp.status_code == 201

    with db_session_factory() as session:
        model = session.get(AIMLModel, model_id)
        assert model.state == ModelState.TRAINING


def test_request_training_while_already_training_cancels_the_orphaned_job(client, db_session_factory):
    """A second RequestTraining against a model that already has a
    RUNNING TrainingJob is treated as the operator's decision to
    supersede it — the earlier job is marked CANCELLED rather than left
    silently RUNNING and unreachable (the old code just overwrote
    model.training_job_id with no trace of the first job left anywhere).
    """
    model_id = _make_model(db_session_factory, ModelState.REGISTERED)
    first = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()

    second = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-2"})
    assert second.status_code == 201
    second_job_id = second.json()["trainingJobId"]
    assert second_job_id != first["trainingJobId"]

    first_status = client.get(f"/training-jobs/{first['trainingJobId']}/status").json()
    assert first_status["status"] == "CANCELLED"

    with db_session_factory() as session:
        model = session.get(AIMLModel, model_id)
        assert str(model.training_job_id) == second_job_id
        assert model.state == ModelState.TRAINING  # unchanged — no FSM transition needed, it was already TRAINING


def test_request_training_rejected_mid_certification_pipeline(client, db_session_factory):
    """A model in TESTED/EMULATED/CERTIFIED/LOADED (mid certification,
    never yet ACTIVE) has no legal TRAIN or RETRAIN transition — this
    must be a clean 409, not an unhandled IllegalTransition crash.
    """
    model_id = _make_model(db_session_factory, ModelState.CERTIFIED)
    resp = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"})
    assert resp.status_code == 409


def _make_group_with_subscription(db_session_factory, member_states, retrain_propagation="ANY_MEMBER_TRIGGERS"):
    """Builds a coordination group with one member per state in
    member_states, plus an MLMFSubscription (with a guard_kpi_floor, so a
    low metric breaches) on the first member — the one whose report
    triggers group evaluation.
    """
    member_ids = [uuid.uuid4() for _ in member_states]
    sub_id = uuid.uuid4()
    with db_session_factory() as session:
        # Distinct model_type per member — a real coordination group's
        # members are distinct models, and (model_type, version) is now
        # a genuine unique constraint (this pass's own fix).
        for i, (member_id, state) in enumerate(zip(member_ids, member_states)):
            session.add(AIMLModel(model_id=member_id, registration_id=str(uuid.uuid4()), model_type=f"t{i}", version="1.0", state=state))
        session.add(MLModelCoordinationGroup(member_model_ids=member_ids, retrain_propagation=retrain_propagation))
        session.add(MLMFSubscription(subscription_id=sub_id, model_id=member_ids[0], metric_types=["accuracy"],
                                      dme_type_id=uuid.uuid4(), guard_kpi_floor={"accuracy": 0.9}))
        session.commit()
    return member_ids, sub_id


def test_group_retrain_trigger_fires_retrain_on_active_members(client, db_session_factory):
    """The actual fix (OPEN_ITEMS.md section 1's MLModelCoordinationGroup
    x SA SMOS convergence item): report_performance used to compute
    groupRetrainTriggered and stop — nothing ever fired RETRAIN on a
    member model.
    """
    member_ids, sub_id = _make_group_with_subscription(db_session_factory, [ModelState.ACTIVE, ModelState.ACTIVE])

    resp = client.post(f"/mlmf/subscriptions/{sub_id}/reports", json={"accuracy": 0.5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["groupRetrainTriggered"] is True
    assert set(body["retrainedModelIds"]) == {str(m) for m in member_ids}

    with db_session_factory() as session:
        for member_id in member_ids:
            model = session.get(AIMLModel, member_id)
            assert model.state == ModelState.TRAINING
            assert model.training_job_id is not None


def test_group_retrain_trigger_skips_non_active_members(client, db_session_factory):
    """RETRAIN is only a legal transition from ACTIVE — a member already
    TRAINING (or never certified) must be skipped, not forced.
    """
    member_ids, sub_id = _make_group_with_subscription(db_session_factory, [ModelState.ACTIVE, ModelState.REGISTERED])

    resp = client.post(f"/mlmf/subscriptions/{sub_id}/reports", json={"accuracy": 0.5})
    body = resp.json()
    assert body["retrainedModelIds"] == [str(member_ids[0])]

    with db_session_factory() as session:
        untouched = session.get(AIMLModel, member_ids[1])
        assert untouched.state == ModelState.REGISTERED
        assert untouched.training_job_id is None


def test_no_group_retrain_when_not_breached(client, db_session_factory):
    member_ids, sub_id = _make_group_with_subscription(db_session_factory, [ModelState.ACTIVE])

    resp = client.post(f"/mlmf/subscriptions/{sub_id}/reports", json={"accuracy": 0.99})
    body = resp.json()
    assert body["breachedFloor"] is False
    assert "groupRetrainTriggered" not in body


def test_get_model_by_id_returns_its_fields(client):
    """OPEN_ITEMS.md section 5: model CRUD was incomplete — only create
    and a type-filtered list existed, no GET-by-id at all.
    """
    created = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"}).json()

    resp = client.get(f"/models/{created['modelId']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["modelId"] == created["modelId"]
    assert body["modelType"] == "coverage-predictor"
    assert body["version"] == "1.0"
    assert body["state"] == ModelState.REGISTERED


def test_register_model_stores_and_exposes_registration_metadata(client):
    """OPEN_ITEMS.md section 5: registration metadata was thin — no I/O
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
    """OPEN_ITEMS.md section 5: the reference's own RegisterModel
    (mmes_apis.go) 409s on a (modelName, modelVersion) unique-constraint
    violation — this build accepted a duplicate silently, creating a
    second, indistinguishable row for the same (modelType, version).
    """
    first = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"})
    assert first.status_code == 201

    resp = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "MODEL_ALREADY_REGISTERED"

    all_models = client.get("/models").json()
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


def test_upload_model_artifact_stamps_version_one_and_records_location(client):
    """OPEN_ITEMS.md section 5: the reference's real UploadModel — ours had
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
    without touching AIMLModel.version at all.
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
    """OPEN_ITEMS.md section 5: model CRUD was incomplete — create, list,
    and (as of the previous pass) get-by-id existed, but no update at all.
    """
    model_id = client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0", "requiredResourceTypeId": "gpu-a"}).json()["modelId"]

    resp = client.put(f"/models/{model_id}", json={
        "modelType": "coverage-predictor", "version": "1.0", "requiredResourceTypeId": "gpu-b",
        "trainingDataLineage": {"source": "dme-type-1"}, "integrityHash": "sha256:abc", "clearedNodeGroups": ["ng1"],
    })
    assert resp.status_code == 200
    assert resp.json()["clearedNodeGroups"] == ["ng1"]

    view = client.get(f"/models/{model_id}").json()
    assert view["clearedNodeGroups"] == ["ng1"]


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


def test_delete_model_cascades_its_artifacts_and_training_jobs(client, db_session_factory):
    """OPEN_ITEMS.md section 5: none of aiml_model's five dependent FKs
    (model_artifact, training_job, model_change_subscription,
    mlmf_subscription, inference_job) had any cascade behavior — the
    same unchecked-FK shape already found and fixed for DME's
    deregister_producer. Deleting a model with dependent rows used to
    either orphan them (SQLite) or crash with an unhandled
    IntegrityError (real Postgres, verified separately against a live
    instance). This proves the application-level cleanup actually
    removes every dependent row, not just the model itself.
    """
    model_id = uuid.UUID(client.post("/models", json={"modelType": "coverage-predictor", "version": "1.0"}).json()["modelId"])
    client.post(f"/models/{model_id}/artifact", files={"file": ("model.zip", b"bytes", "application/zip")})
    client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"})

    with db_session_factory() as session:
        session.add(ModelChangeSubscription(model_id=model_id, consumer_id="rapp-2"))
        sub = MLMFSubscription(model_id=model_id, metric_types=["accuracy"], dme_type_id=uuid.uuid4())
        session.add(sub)
        session.add(InferenceJob(model_id=model_id))
        session.commit()
        subscription_id = sub.subscription_id
        session.add(PerformanceReport(subscription_id=subscription_id, metrics={"accuracy": 0.1}, breached_floor=True))
        session.commit()

    resp = client.delete(f"/models/{model_id}")
    assert resp.status_code == 204

    with db_session_factory() as session:
        assert session.get(AIMLModel, model_id) is None
        assert session.query(ModelArtifact).filter(ModelArtifact.model_id == model_id).count() == 0
        assert session.query(TrainingJob).filter(TrainingJob.model_id == model_id).count() == 0
        assert session.query(ModelChangeSubscription).filter(ModelChangeSubscription.model_id == model_id).count() == 0
        assert session.query(MLMFSubscription).filter(MLMFSubscription.model_id == model_id).count() == 0
        assert session.query(InferenceJob).filter(InferenceJob.model_id == model_id).count() == 0
        assert session.query(PerformanceReport).filter(PerformanceReport.subscription_id == subscription_id).count() == 0
