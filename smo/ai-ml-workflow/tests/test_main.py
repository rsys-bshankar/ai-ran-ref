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
from app.models import AIMLModel, FeatureGroup, InferenceJob, MLMFSubscription, MLModelCoordinationGroup, ModelArtifact, ModelChangeSubscription, PerformanceReport, TrainingJob
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
        ModelChangeSubscription.__table__, InferenceJob.__table__, FeatureGroup.__table__,
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


def _make_model(db_session_factory, state, model_type="t") -> uuid.UUID:
    model_id = uuid.uuid4()
    with db_session_factory() as session:
        session.add(AIMLModel(model_id=model_id, registration_id=str(uuid.uuid4()), model_type=model_type, version="1.0", state=state))
        session.commit()
    return model_id


def test_request_training_on_registered_model_fires_train(client, db_session_factory):
    model_id = _make_model(db_session_factory, ModelState.REGISTERED)
    resp = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"})
    assert resp.status_code == 201

    with db_session_factory() as session:
        model = session.get(AIMLModel, model_id)
        assert model.state == ModelState.TRAINING


def test_request_training_ml_training_type_initial_then_retrain(client, db_session_factory):
    """TS28.105 AI/ML NRM's own real mLTrainingType (SPEC_AUDIT.md) —
    INITIAL_TRAINING the very first cycle (model still REGISTERED),
    RE_TRAINING every subsequent one. Already computed internally as an
    FSM event choice, but never stored or returned until this.
    """
    model_id = _make_model(db_session_factory, ModelState.REGISTERED)
    first_id = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()["trainingJobId"]
    assert client.get(f"/training-jobs/{first_id}/status").json()["mlTrainingType"] == "INITIAL_TRAINING"

    with db_session_factory() as session:
        model = session.get(AIMLModel, model_id)
        model.state = ModelState.ACTIVE
        session.commit()

    second_id = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()["trainingJobId"]
    assert client.get(f"/training-jobs/{second_id}/status").json()["mlTrainingType"] == "RE_TRAINING"


def test_request_training_stores_and_exposes_extended_fields(client, db_session_factory):
    """OPEN_ITEMS.md section 5: TrainingJob was far thinner than the
    reference's own TrainingJob (trainingmgr/models/trainingjob.py) —
    no run_id, no distinct training/validation dataset fields, no
    separate consumer/producer rApp ids.
    """
    model_id = _make_model(db_session_factory, ModelState.REGISTERED)
    resp = client.post("/training-jobs", json={
        "modelId": str(model_id), "producerId": "rapp-1", "runId": "run-42",
        "trainingDataset": "s3://bucket/train.csv", "validationDataset": "s3://bucket/val.csv",
        "consumerRappId": "rapp-consumer", "producerRappId": "rapp-producer",
    })
    training_job_id = resp.json()["trainingJobId"]

    status = client.get(f"/training-jobs/{training_job_id}/status").json()
    assert status["runId"] == "run-42"
    assert status["trainingDataset"] == "s3://bucket/train.csv"
    assert status["validationDataset"] == "s3://bucket/val.csv"
    assert status["consumerRappId"] == "rapp-consumer"
    assert status["producerRappId"] == "rapp-producer"


def test_update_and_get_training_job_model_metrics(client, db_session_factory):
    """OPEN_ITEMS.md section 5: no metrics-writeback endpoint existed at
    all. The reference's own POST .../update-model-metrics/<id> replaces
    model_metrics wholesale, not a merge.
    """
    model_id = _make_model(db_session_factory, ModelState.REGISTERED)
    training_job_id = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()["trainingJobId"]

    resp = client.post(f"/training-jobs/{training_job_id}/model-metrics", json={"accuracy": 0.9})
    assert resp.status_code == 200
    assert resp.json()["modelMetrics"] == {"accuracy": 0.9}

    get_resp = client.get(f"/training-jobs/{training_job_id}/model-metrics")
    assert get_resp.json() == {"accuracy": 0.9}

    # a second update replaces, it doesn't merge
    client.post(f"/training-jobs/{training_job_id}/model-metrics", json={"f1": 0.8})
    assert client.get(f"/training-jobs/{training_job_id}/model-metrics").json() == {"f1": 0.8}


def test_get_model_metrics_for_unknown_training_job_is_404(client):
    resp = client.get(f"/training-jobs/{uuid.uuid4()}/model-metrics")
    assert resp.status_code == 404


def test_update_model_metrics_for_unknown_training_job_is_404(client):
    resp = client.post(f"/training-jobs/{uuid.uuid4()}/model-metrics", json={"accuracy": 0.9})
    assert resp.status_code == 404


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


def _feature_group_body(feature_group_name="cellCounters", **extra):
    return {
        "featureGroupName": feature_group_name, "featureList": "throughput,latency", "datalakeSource": "influxdb",
        "host": "influxdb.smo", "port": "8086", "bucket": "ran-metrics", "token": "secret-token",
        "dbOrg": "smo-org", "measurement": "cell_kpis", **extra,
    }


def test_create_feature_group_returns_its_fields(client):
    """OPEN_ITEMS.md section 5: no feature-group/feature-store concept
    existed at all — the reference's own FeatureGroup
    (aiml-fw-awmf-tm's featuregroup.py/featuregroup_controller.py).
    """
    resp = client.post("/feature-groups", json=_feature_group_body())
    assert resp.status_code == 201
    body = resp.json()
    assert body["featureGroupName"] == "cellCounters"
    assert body["featureList"] == "throughput,latency"
    assert body["enableDme"] is False
    assert "featureGroupId" in body


def test_create_feature_group_rejects_a_duplicate_name(client):
    """CreateFeatureGroup's own DBException("already exist") path, 409."""
    first = client.post("/feature-groups", json=_feature_group_body())
    assert first.status_code == 201

    resp = client.post("/feature-groups", json=_feature_group_body())
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "FEATURE_GROUP_ALREADY_REGISTERED"


def test_create_feature_group_rejects_a_name_that_is_too_short(client):
    """The reference's own name-length check: 3-63 characters."""
    resp = client.post("/feature-groups", json=_feature_group_body(feature_group_name="ab"))
    assert resp.status_code == 400
    assert resp.json()["detail"]["title"] == "FEATURE_GROUP_NAME_INVALID"


def test_create_feature_group_rejects_a_name_with_non_word_characters(client):
    """The reference's own PATTERN = \\w+ — no hyphens, spaces, or dots."""
    resp = client.post("/feature-groups", json=_feature_group_body(feature_group_name="cell-counters"))
    assert resp.status_code == 400
    assert resp.json()["detail"]["title"] == "FEATURE_GROUP_NAME_INVALID"


def test_list_feature_groups_returns_registered_groups(client):
    client.post("/feature-groups", json=_feature_group_body(feature_group_name="cellCounters"))
    client.post("/feature-groups", json=_feature_group_body(feature_group_name="handoverCounters"))

    resp = client.get("/feature-groups")
    assert resp.status_code == 200
    names = {g["featureGroupName"] for g in resp.json()["featureGroups"]}
    assert names == {"cellCounters", "handoverCounters"}


def test_list_feature_groups_returns_empty_list_when_none_registered(client):
    resp = client.get("/feature-groups")
    assert resp.json() == {"featureGroups": []}


def test_create_feature_group_stores_enable_dme_and_dme_fields(client):
    """enableDme is stored and returned faithfully — the real DME job
    creation it would trigger in the reference is a deliberate elision,
    not silently dropped data.
    """
    resp = client.post("/feature-groups", json=_feature_group_body(
        enableDme=True, sourceName="ran-nf-oam", dmePort="8000", measuredObjClass="NRCellDU",
    ))
    body = resp.json()
    assert body["enableDme"] is True
    assert body["sourceName"] == "ran-nf-oam"
    assert body["dmePort"] == "8000"
    assert body["measuredObjClass"] == "NRCellDU"


def test_health_check_answers_the_gui_bff_liveness_probe(client):
    """GUI pass: the BFF's /modules/status probes /<module>/health on every module."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


def test_list_training_jobs_filters_by_model_and_status(client, db_session_factory):
    """GUI pass: only a per-id status read existed for training jobs."""
    model_a = _make_model(db_session_factory, ModelState.REGISTERED)
    with db_session_factory() as session:
        session.add(AIMLModel(model_id=(model_b := uuid.uuid4()), registration_id="r", model_type="t", version="2.0", state=ModelState.REGISTERED))
        session.commit()
    job_a = client.post("/training-jobs", json={"modelId": str(model_a), "producerId": "rapp-1"}).json()["trainingJobId"]
    client.post("/training-jobs", json={"modelId": str(model_b), "producerId": "rapp-1"})

    assert len(client.get("/training-jobs").json()) == 2
    only_a = client.get("/training-jobs", params={"model_id": str(model_a)}).json()
    assert [j["trainingJobId"] for j in only_a] == [job_a]
    assert only_a[0]["status"] == "RUNNING" and only_a[0]["modelId"] == str(model_a)

    client.delete(f"/training-jobs/{job_a}")
    assert [j["trainingJobId"] for j in client.get("/training-jobs", params={"status": "CANCELLED"}).json()] == [job_a]


def test_list_inference_jobs_filters_by_model(client, db_session_factory):
    model_id = _make_model(db_session_factory, ModelState.ACTIVE)
    job_id = client.post(f"/models/{model_id}/inference-jobs").json()["inferenceJobId"]

    listed = client.get("/inference-jobs", params={"model_id": str(model_id)}).json()
    assert listed == [{"inferenceJobId": job_id, "modelId": str(model_id), "status": "RUNNING", "notificationDestination": None}]
    assert client.get("/inference-jobs", params={"status": "COMPLETED"}).json() == []


def test_list_coordination_groups_returns_members(client, db_session_factory):
    model_id_1 = _make_model(db_session_factory, ModelState.ACTIVE, model_type="t1")
    model_id_2 = _make_model(db_session_factory, ModelState.ACTIVE, model_type="t2")
    group_id = client.post("/coordination-groups", json={"memberModelIds": [str(model_id_1), str(model_id_2)]}).json()["groupId"]

    groups = client.get("/coordination-groups").json()
    assert [(g["groupId"], g["memberModelIds"]) for g in groups] == [(group_id, [str(model_id_1), str(model_id_2)])]


def test_create_coordination_group_rejects_fewer_than_two_members(client, db_session_factory):
    """The migration's own CHECK constraint (array_length >= 2) enforces
    this at the DB layer, but SQLite's test schema (built from the ORM
    models, which never mirrored the constraint) doesn't — so only a
    real-Postgres run ever caught the unhandled IntegrityError this used
    to raise. Pre-validated here now instead, matching RequestTraining's
    own exactly_one_target pre-check.
    """
    model_id = _make_model(db_session_factory, ModelState.ACTIVE)
    resp = client.post("/coordination-groups", json={"memberModelIds": [str(model_id)]})
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "COORDINATION_GROUP_TOO_SMALL"

    resp = client.post("/coordination-groups", json={"memberModelIds": []})
    assert resp.status_code == 422


def test_list_mlmf_subscriptions_and_their_reports_newest_first(client, db_session_factory):
    """GUI pass: MLMF subscriptions/reports were write-only."""
    model_id = _make_model(db_session_factory, ModelState.ACTIVE)
    sub_id = client.post("/mlmf/subscriptions", params={"model_id": str(model_id), "dme_type_id": str(uuid.uuid4())},
                          json={"metric_types": ["accuracy"], "guard_kpi_floor": {"accuracy": 0.9}}).json()["subscriptionId"]
    client.post(f"/mlmf/subscriptions/{sub_id}/reports", json={"accuracy": 0.95})
    client.post(f"/mlmf/subscriptions/{sub_id}/reports", json={"accuracy": 0.5})

    subs = client.get("/mlmf/subscriptions", params={"model_id": str(model_id)}).json()
    assert [(s["subscriptionId"], s["guardKpiFloor"]) for s in subs] == [(sub_id, {"accuracy": 0.9})]

    reports = client.get(f"/mlmf/subscriptions/{sub_id}/reports").json()
    assert [(r["metrics"]["accuracy"], r["breachedFloor"]) for r in reports] == [(0.5, True), (0.95, False)]

    breached = client.get("/mlmf/reports", params={"breached_only": True}).json()
    assert [r["metrics"]["accuracy"] for r in breached] == [0.5]
    assert len(client.get("/mlmf/reports").json()) == 2


def test_list_mlmf_reports_404_on_an_unknown_subscription(client):
    assert client.get(f"/mlmf/subscriptions/{uuid.uuid4()}/reports").status_code == 404
