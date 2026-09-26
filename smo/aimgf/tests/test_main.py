"""Tests for AIMgF's routes (AI/ML Workflow LLD sections 1, 4).
Run with: pytest smo/aimgf/tests -q

Wave 1's split moved MLModel/MLModelCoordinationGroup to MLMR's own
process — AIMgF now reads/writes model state through R1Client rather
than a direct ORM import, so these tests fake MLMR's two routes
(`GET /mlmr/models/{id}`, `PATCH /mlmr/models/{id}/lifecycle`,
`GET /mlmr/coordination-groups`) with a small stateful in-memory double,
the same "monkeypatch app.main.R1Client.<verb>" shape this build already
uses for e.g. nfo/tests/test_main.py's own FOCOM double. The real
cross-service round trip (an actual MLMR process backing these calls) is
covered by tests_integration/test_demo_runbook.py instead.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from smo_shared.db import Base, get_session
from smo_shared.testing import make_test_engine

from app.main import app
from app.models import FeatureGroup, InferenceJob, MLMFSubscription, PerformanceReport, TrainingJob
from app.statemachine import ModelState


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeMlmr:
    """A minimal, stateful double for MLMR's own routes — enough to drive
    AIMgF's FSM-firing logic exactly as the real service would, without
    standing up a second FastAPI app for what are meant to be AIMgF's own
    unit tests.
    """

    def __init__(self):
        self.models: dict[str, dict] = {}
        self.groups: list[dict] = []

    def add_model(self, model_id, state, training_job_id=None) -> uuid.UUID:
        model_id = model_id or uuid.uuid4()
        self.models[str(model_id)] = {
            "modelId": str(model_id), "state": state,
            "trainingJobId": str(training_job_id) if training_job_id else None,
            "clearedNodeGroups": [],
        }
        return model_id

    def get(self, path, **kw):
        if path == "/mlmr/coordination-groups":
            return FakeResponse(200, self.groups)
        model_id = path.rsplit("/", 1)[-1]
        model = self.models.get(model_id)
        return FakeResponse(200, model) if model is not None else FakeResponse(404, {"detail": "no such model"})

    def patch(self, path, json=None, **kw):
        model_id = path.split("/")[3]  # /mlmr/models/{id}/lifecycle
        model = self.models[model_id]
        if json.get("state") is not None:
            model["state"] = json["state"]
        if json.get("trainingJobId") is not None:
            model["trainingJobId"] = json["trainingJobId"]
        if json.get("clearedNodeGroups") is not None:
            model["clearedNodeGroups"] = json["clearedNodeGroups"]
        return FakeResponse(200, model)


@pytest.fixture
def db_session_factory():
    engine = make_test_engine()
    Base.metadata.create_all(engine, tables=[
        TrainingJob.__table__, MLMFSubscription.__table__, PerformanceReport.__table__,
        InferenceJob.__table__, FeatureGroup.__table__,
    ])
    return sessionmaker(bind=engine)


@pytest.fixture
def mlmr(monkeypatch):
    fake = FakeMlmr()
    monkeypatch.setattr("app.main.R1Client.get", lambda self, path, **kw: fake.get(path, **kw))
    monkeypatch.setattr("app.main.R1Client.patch", lambda self, path, json=None, **kw: fake.patch(path, json=json, **kw))
    return fake


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


def test_request_training_on_registered_model_fires_train(client, mlmr):
    model_id = mlmr.add_model(None, ModelState.REGISTERED)
    resp = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"})
    assert resp.status_code == 201
    assert mlmr.models[str(model_id)]["state"] == ModelState.TRAINING


def test_request_training_ml_training_type_initial_then_retrain(client, mlmr):
    """TS28.105 AI/ML NRM's own real mLTrainingType (SPEC_AUDIT.md) —
    INITIAL_TRAINING the very first cycle (model still REGISTERED),
    RE_TRAINING every subsequent one.
    """
    model_id = mlmr.add_model(None, ModelState.REGISTERED)
    first_id = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()["trainingJobId"]
    assert client.get(f"/training-jobs/{first_id}/status").json()["mlTrainingType"] == "INITIAL_TRAINING"

    mlmr.models[str(model_id)]["state"] = ModelState.ACTIVE

    second_id = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()["trainingJobId"]
    assert client.get(f"/training-jobs/{second_id}/status").json()["mlTrainingType"] == "RE_TRAINING"


def test_request_training_stores_and_exposes_extended_fields(client, mlmr):
    """OPEN_ITEMS.md section 5: TrainingJob was far thinner than the
    reference's own TrainingJob (trainingmgr/models/trainingjob.py).
    """
    model_id = mlmr.add_model(None, ModelState.REGISTERED)
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


def test_update_and_get_training_job_model_metrics(client, mlmr):
    model_id = mlmr.add_model(None, ModelState.REGISTERED)
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


def test_request_training_on_active_model_fires_retrain_not_train(client, mlmr):
    """The actual bug this pass fixed: RequestTraining always fired TRAIN
    regardless of the model's state, which is only a legal transition
    from REGISTERED.
    """
    model_id = mlmr.add_model(None, ModelState.ACTIVE)
    resp = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"})
    assert resp.status_code == 201
    assert mlmr.models[str(model_id)]["state"] == ModelState.TRAINING


def test_request_training_while_already_training_cancels_the_orphaned_job(client, mlmr):
    """A second RequestTraining against a model that already has a
    RUNNING TrainingJob is treated as the operator's decision to
    supersede it — the earlier job is marked CANCELLED rather than left
    silently RUNNING and unreachable.
    """
    model_id = mlmr.add_model(None, ModelState.REGISTERED)
    first = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()

    second = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-2"})
    assert second.status_code == 201
    second_job_id = second.json()["trainingJobId"]
    assert second_job_id != first["trainingJobId"]

    first_status = client.get(f"/training-jobs/{first['trainingJobId']}/status").json()
    assert first_status["status"] == "CANCELLED"

    assert mlmr.models[str(model_id)]["trainingJobId"] == second_job_id
    assert mlmr.models[str(model_id)]["state"] == ModelState.TRAINING  # unchanged — no FSM transition needed, it was already TRAINING


def test_request_training_rejected_mid_certification_pipeline(client, mlmr):
    """A model in TESTED/EMULATED/CERTIFIED/LOADED (mid certification,
    never yet ACTIVE) has no legal TRAIN or RETRAIN transition — this
    must be a clean 409, not an unhandled IllegalTransition crash.
    """
    model_id = mlmr.add_model(None, ModelState.CERTIFIED)
    resp = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"})
    assert resp.status_code == 409


def _make_group_with_subscription(mlmr, member_states, retrain_propagation="ANY_MEMBER_TRIGGERS"):
    """Builds a coordination group with one member per state in
    member_states, plus an MLMFSubscription (with a guard_kpi_floor, so a
    low metric breaches) on the first member — the one whose report
    triggers group evaluation.
    """
    member_ids = [mlmr.add_model(None, state) for state in member_states]
    mlmr.groups.append({"groupId": str(uuid.uuid4()), "memberModelIds": [str(m) for m in member_ids], "retrainPropagation": retrain_propagation})
    return member_ids


def test_group_retrain_trigger_fires_retrain_on_active_members(client, mlmr, db_session_factory):
    """The actual fix (OPEN_ITEMS.md section 1's MLModelCoordinationGroup
    x SA SMOS convergence item): report_performance used to compute
    groupRetrainTriggered and stop — nothing ever fired RETRAIN on a
    member model.
    """
    member_ids = _make_group_with_subscription(mlmr, [ModelState.ACTIVE, ModelState.ACTIVE])
    with db_session_factory() as session:
        sub = MLMFSubscription(model_id=member_ids[0], metric_types=["accuracy"], dme_type_id=uuid.uuid4(), guard_kpi_floor={"accuracy": 0.9})
        session.add(sub)
        session.commit()
        sub_id = sub.subscription_id

    resp = client.post(f"/mlmf/subscriptions/{sub_id}/reports", json={"accuracy": 0.5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["groupRetrainTriggered"] is True
    assert set(body["retrainedModelIds"]) == {str(m) for m in member_ids}

    for member_id in member_ids:
        assert mlmr.models[str(member_id)]["state"] == ModelState.TRAINING
        assert mlmr.models[str(member_id)]["trainingJobId"] is not None


def test_group_retrain_trigger_skips_non_active_members(client, mlmr, db_session_factory):
    """RETRAIN is only a legal transition from ACTIVE — a member already
    TRAINING (or never certified) must be skipped, not forced.
    """
    member_ids = _make_group_with_subscription(mlmr, [ModelState.ACTIVE, ModelState.REGISTERED])
    with db_session_factory() as session:
        sub = MLMFSubscription(model_id=member_ids[0], metric_types=["accuracy"], dme_type_id=uuid.uuid4(), guard_kpi_floor={"accuracy": 0.9})
        session.add(sub)
        session.commit()
        sub_id = sub.subscription_id

    resp = client.post(f"/mlmf/subscriptions/{sub_id}/reports", json={"accuracy": 0.5})
    body = resp.json()
    assert body["retrainedModelIds"] == [str(member_ids[0])]

    untouched = mlmr.models[str(member_ids[1])]
    assert untouched["state"] == ModelState.REGISTERED
    assert untouched["trainingJobId"] is None


def test_no_group_retrain_when_not_breached(client, mlmr, db_session_factory):
    member_ids = _make_group_with_subscription(mlmr, [ModelState.ACTIVE])
    with db_session_factory() as session:
        sub = MLMFSubscription(model_id=member_ids[0], metric_types=["accuracy"], dme_type_id=uuid.uuid4(), guard_kpi_floor={"accuracy": 0.9})
        session.add(sub)
        session.commit()
        sub_id = sub.subscription_id

    resp = client.post(f"/mlmf/subscriptions/{sub_id}/reports", json={"accuracy": 0.99})
    body = resp.json()
    assert body["breachedFloor"] is False
    assert "groupRetrainTriggered" not in body


def test_advance_model_lifecycle_fires_the_requested_event(client, mlmr):
    model_id = mlmr.add_model(None, ModelState.TRAINING)
    resp = client.post(f"/models/{model_id}/advance", params={"event": "TRAINING_COMPLETE"})
    assert resp.status_code == 200
    assert resp.json()["state"] == ModelState.TESTED
    assert mlmr.models[str(model_id)]["state"] == ModelState.TESTED


def test_advance_model_lifecycle_for_unknown_model_is_404(client, mlmr):
    resp = client.post(f"/models/{uuid.uuid4()}/advance", params={"event": "CERTIFY"})
    assert resp.status_code == 404


def test_request_inference_requires_active_model(client, mlmr):
    model_id = mlmr.add_model(None, ModelState.CERTIFIED)
    resp = client.post(f"/models/{model_id}/inference-jobs")
    assert resp.status_code == 409


def test_request_inference_on_active_model_creates_a_running_job(client, mlmr):
    model_id = mlmr.add_model(None, ModelState.ACTIVE)
    resp = client.post(f"/models/{model_id}/inference-jobs")
    assert resp.status_code == 201
    job_id = resp.json()["inferenceJobId"]
    assert client.get(f"/inference-jobs/{job_id}/status").json()["status"] == "RUNNING"


def test_health_check_answers_the_gui_bff_liveness_probe(client):
    """GUI pass: the BFF's /modules/status probes /<module>/health on every module."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


def test_list_training_jobs_filters_by_model_and_status(client, mlmr):
    """GUI pass: only a per-id status read existed for training jobs."""
    model_a = mlmr.add_model(None, ModelState.REGISTERED)
    model_b = mlmr.add_model(None, ModelState.REGISTERED)
    job_a = client.post("/training-jobs", json={"modelId": str(model_a), "producerId": "rapp-1"}).json()["trainingJobId"]
    client.post("/training-jobs", json={"modelId": str(model_b), "producerId": "rapp-1"})

    assert len(client.get("/training-jobs").json()) == 2
    only_a = client.get("/training-jobs", params={"model_id": str(model_a)}).json()
    assert [j["trainingJobId"] for j in only_a] == [job_a]
    assert only_a[0]["status"] == "RUNNING" and only_a[0]["modelId"] == str(model_a)

    client.delete(f"/training-jobs/{job_a}")
    assert [j["trainingJobId"] for j in client.get("/training-jobs", params={"status": "CANCELLED"}).json()] == [job_a]


def test_list_inference_jobs_filters_by_model(client, mlmr):
    model_id = mlmr.add_model(None, ModelState.ACTIVE)
    job_id = client.post(f"/models/{model_id}/inference-jobs").json()["inferenceJobId"]

    listed = client.get("/inference-jobs", params={"model_id": str(model_id)}).json()
    assert listed == [{"inferenceJobId": job_id, "modelId": str(model_id), "status": "RUNNING", "notificationDestination": None}]
    assert client.get("/inference-jobs", params={"status": "COMPLETED"}).json() == []


def test_list_mlmf_subscriptions_and_their_reports_newest_first(client, mlmr):
    """GUI pass: MLMF subscriptions/reports were write-only."""
    model_id = mlmr.add_model(None, ModelState.ACTIVE)
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
