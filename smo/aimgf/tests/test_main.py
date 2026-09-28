"""Tests for AIMgF's routes (AI/ML Workflow LLD sections 1, 4; Wave 2's
own ModelLifecycle/RuntimeLifecycle FSMs and domain model).
Run with: pytest smo/aimgf/tests -q

AIMgF reads/writes MLMR's model-existence check and coordination-group
listing through R1Client, and (since Wave 2) calls NFO's descriptor/
instantiate/scale/terminate routes for its own RuntimeLifecycle — both
faked here with small stateful in-memory doubles, the same "monkeypatch
app.main.R1Client.<verb>" shape this build already uses for e.g.
nfo/tests/test_main.py's own FOCOM double. AIMgF's own ModelLifecycle
state, unlike Wave 1, is real — a genuine row in this module's own test
DB, not faked. The real cross-service round trip (actual MLMR/NFO
processes backing these calls) is covered by
tests_integration/test_demo_runbook.py instead.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from smo_shared.db import Base, get_session
from smo_shared.testing import make_test_engine

from app.main import app
from app.models import (
    CertificationRecord, EmulationJob, FeatureGroup, InferenceJob, LifecycleTransition, MLMFSubscription,
    ModelLifecycle, PerformanceReport, TrainingJob, ValidationJob,
)
from app.statemachine import ModelLifecycleState, RuntimeLifecycleState


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeMlmr:
    """A minimal double for MLMR's own model-existence check
    (`GET /mlmr/models/{id}`) and coordination-group listing
    (`GET /mlmr/coordination-groups`) — AIMgF's own lifecycle state lives
    in its own real `model_lifecycle` table now (Wave 2), not faked here.
    """

    def __init__(self):
        self.models: dict[str, dict] = {}
        self.groups: list[dict] = []

    def add_model(self, model_id=None) -> uuid.UUID:
        model_id = model_id or uuid.uuid4()
        self.models[str(model_id)] = {"modelId": str(model_id)}
        return model_id

    def get(self, path, **kw):
        if path == "/mlmr/coordination-groups":
            return FakeResponse(200, {"items": self.groups, "total": len(self.groups), "limit": 100, "offset": 0})
        model_id = path.rsplit("/", 1)[-1]
        model = self.models.get(model_id)
        return FakeResponse(200, model) if model is not None else FakeResponse(
            404, {"detail": {"type": "about:blank", "title": "MODEL_NOT_FOUND", "status": 404, "detail": "no such model"}}
        )


class FakeNfo:
    """A minimal double for NFO's own descriptor/instantiate/scale/
    terminate routes — enough to drive AIMgF's RuntimeLifecycle exactly
    as the real service would.
    """

    def __init__(self):
        self.descriptors: dict[str, dict] = {}
        self.deployments: dict[str, dict] = {}

    def post(self, path, json=None, **kw):
        if path == "/nfo/descriptors":
            descriptor_id = str(uuid.uuid4())
            self.descriptors[descriptor_id] = json
            return FakeResponse(201, {"nfDeploymentDescriptorId": descriptor_id})
        if path == "/nfo/deployments":
            deployment_id = str(uuid.uuid4())
            self.deployments[deployment_id] = {"state": "RUNNING", "scaled": 0, "descriptorId": json["nfDeploymentDescriptorId"]}
            return FakeResponse(202, {"nfDeploymentId": deployment_id, "state": "RUNNING"})
        if path.endswith("/scale"):
            deployment_id = path.split("/")[3]
            self.deployments[deployment_id]["scaled"] += 1
            return FakeResponse(200, {"nfDeploymentId": deployment_id, "state": "RUNNING"})
        raise AssertionError(f"unexpected NFO POST {path}")

    def delete(self, path, **kw):
        deployment_id = path.rsplit("/", 1)[-1]
        self.deployments.pop(deployment_id, None)
        return FakeResponse(204, None)


@pytest.fixture
def db_session_factory():
    engine = make_test_engine()
    Base.metadata.create_all(engine, tables=[
        ModelLifecycle.__table__, ValidationJob.__table__, EmulationJob.__table__, CertificationRecord.__table__,
        LifecycleTransition.__table__, TrainingJob.__table__, MLMFSubscription.__table__, PerformanceReport.__table__,
        InferenceJob.__table__, FeatureGroup.__table__,
    ])
    return sessionmaker(bind=engine)


@pytest.fixture
def mlmr(monkeypatch):
    fake_mlmr = FakeMlmr()
    fake_nfo = FakeNfo()

    def fake_get(self, path, **kw):
        return fake_mlmr.get(path, **kw)

    def fake_post(self, path, json=None, **kw):
        return fake_nfo.post(path, json=json, **kw)

    def fake_delete(self, path, **kw):
        return fake_nfo.delete(path, **kw)

    monkeypatch.setattr("app.main.R1Client.get", fake_get)
    monkeypatch.setattr("app.main.R1Client.post", fake_post)
    monkeypatch.setattr("app.main.R1Client.delete", fake_delete)
    fake_mlmr.nfo = fake_nfo
    return fake_mlmr


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


def _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=None, runtime_lifecycle_state=None) -> None:
    """Test-only shortcut: writes AIMgF's own ModelLifecycle row directly,
    the same role mlmr/tests' own `_make_model(..., state)` played before
    Wave 2 — most tests care about "a model already at CERTIFIED", not
    about walking every intermediate FSM transition to get there.
    """
    with db_session_factory() as session:
        lifecycle = session.get(ModelLifecycle, model_id)
        if lifecycle is None:
            lifecycle = ModelLifecycle(model_id=model_id)
            session.add(lifecycle)
        if model_lifecycle_state is not None:
            lifecycle.model_lifecycle_state = model_lifecycle_state
        if runtime_lifecycle_state is not None:
            lifecycle.runtime_lifecycle_state = runtime_lifecycle_state
        session.commit()


# ---------------------------------------------------------------- Training

def test_request_training_on_registered_model_fires_create_training(client, mlmr):
    model_id = mlmr.add_model()
    resp = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"})
    assert resp.status_code == 201
    assert client.get(f"/models/{model_id}/lifecycle").json()["modelLifecycleState"] == ModelLifecycleState.TRAINING


def test_request_training_ml_training_type_initial_then_retrain(client, mlmr, db_session_factory):
    """TS28.105 AI/ML NRM's own real mLTrainingType (SPEC_AUDIT.md) —
    INITIAL_TRAINING the very first cycle (model still REGISTERED),
    RE_TRAINING every subsequent one.
    """
    model_id = mlmr.add_model()
    first_id = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()["trainingJobId"]
    assert client.get(f"/training-jobs/{first_id}/status").json()["mlTrainingType"] == "INITIAL_TRAINING"

    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.PROMOTED)

    second_id = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()["trainingJobId"]
    assert client.get(f"/training-jobs/{second_id}/status").json()["mlTrainingType"] == "RE_TRAINING"


def test_request_training_stores_and_exposes_extended_fields(client, mlmr):
    """OPEN_ITEMS.md section 5: TrainingJob was far thinner than the
    reference's own TrainingJob (trainingmgr/models/trainingjob.py).
    """
    model_id = mlmr.add_model()
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
    model_id = mlmr.add_model()
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


# ---------------------------------------------------------------- Wave 3: suspend/resume

def test_suspend_and_resume_a_running_training_job(client, mlmr):
    model_id = mlmr.add_model()
    job_id = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()["trainingJobId"]

    suspend = client.post(f"/training-jobs/{job_id}/suspend")
    assert suspend.status_code == 200
    assert suspend.json()["status"] == "SUSPENDED"
    assert client.get(f"/training-jobs/{job_id}/status").json()["status"] == "SUSPENDED"

    resume = client.post(f"/training-jobs/{job_id}/resume")
    assert resume.status_code == 200
    assert resume.json()["status"] == "IN_PROGRESS"
    assert client.get(f"/training-jobs/{job_id}/status").json()["status"] == "IN_PROGRESS"


def test_suspend_does_not_touch_model_lifecycle_state(client, mlmr):
    """Wave 2's two real FSMs operate one level up — a job-level suspend
    is deliberately not a third state machine and must not reach into
    ModelLifecycleState, the same way FINISHED/FAILED/CANCELLED
    transitions on job.status already don't either.
    """
    model_id = mlmr.add_model()
    job_id = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()["trainingJobId"]
    client.post(f"/training-jobs/{job_id}/suspend")
    assert client.get(f"/models/{model_id}/lifecycle").json()["modelLifecycleState"] == ModelLifecycleState.TRAINING


def test_suspend_rejects_a_non_running_job(client, mlmr):
    model_id = mlmr.add_model()
    job_id = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()["trainingJobId"]
    client.delete(f"/training-jobs/{job_id}")  # cancel it first

    resp = client.post(f"/training-jobs/{job_id}/suspend")
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "TRAINING_JOB_ILLEGAL_TRANSITION"


def test_resume_rejects_a_non_suspended_job(client, mlmr):
    model_id = mlmr.add_model()
    job_id = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()["trainingJobId"]

    resp = client.post(f"/training-jobs/{job_id}/resume")
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "TRAINING_JOB_ILLEGAL_TRANSITION"


def test_suspend_unknown_training_job_is_404(client):
    resp = client.post(f"/training-jobs/{uuid.uuid4()}/suspend")
    assert resp.status_code == 404


def test_resume_unknown_training_job_is_404(client):
    resp = client.post(f"/training-jobs/{uuid.uuid4()}/resume")
    assert resp.status_code == 404


def test_request_training_on_promoted_model_fires_create_training_not_a_shortcut(client, mlmr, db_session_factory):
    """The actual Wave 1 fix, carried over: RequestTraining must not
    always fire the same transition regardless of the model's state —
    CREATE_TRAINING is only legal from REGISTERED/PROMOTED/FAILED/TRAINING.
    """
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.PROMOTED)
    resp = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"})
    assert resp.status_code == 201
    assert client.get(f"/models/{model_id}/lifecycle").json()["modelLifecycleState"] == ModelLifecycleState.TRAINING


def test_request_training_while_already_training_cancels_the_orphaned_job(client, mlmr):
    """A second RequestTraining against a model that already has an
    IN_PROGRESS TrainingJob is treated as the operator's decision to
    supersede it — the earlier job is marked CANCELLED rather than left
    silently IN_PROGRESS and unreachable.
    """
    model_id = mlmr.add_model()
    first = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()

    second = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-2"})
    assert second.status_code == 201
    second_job_id = second.json()["trainingJobId"]
    assert second_job_id != first["trainingJobId"]

    first_status = client.get(f"/training-jobs/{first['trainingJobId']}/status").json()
    assert first_status["status"] == "CANCELLED"

    lifecycle = client.get(f"/models/{model_id}/lifecycle").json()
    assert lifecycle["trainingJobId"] == second_job_id
    assert lifecycle["modelLifecycleState"] == ModelLifecycleState.TRAINING  # unchanged — no FSM transition needed, it was already TRAINING


def test_request_training_rejected_mid_certification_pipeline(client, mlmr, db_session_factory):
    """A model mid certification (never yet PROMOTED) has no legal
    CREATE_TRAINING transition — this must be a clean 409, not an
    unhandled IllegalTransition crash.
    """
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.CERTIFIED)
    resp = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"})
    assert resp.status_code == 409


def _make_group_with_subscription(mlmr, db_session_factory, member_states, retrain_propagation="ANY_MEMBER_TRIGGERS"):
    """Builds a coordination group with one member per ModelLifecycleState
    in member_states, plus an MLMFSubscription (with a guard_kpi_floor, so
    a low metric breaches) on the first member — the one whose report
    triggers group evaluation.
    """
    member_ids = [mlmr.add_model() for _ in member_states]
    for member_id, state in zip(member_ids, member_states):
        _set_lifecycle(db_session_factory, member_id, model_lifecycle_state=state)
    mlmr.groups.append({"groupId": str(uuid.uuid4()), "memberModelIds": [str(m) for m in member_ids], "retrainPropagation": retrain_propagation})
    return member_ids


def test_group_retrain_trigger_fires_create_training_on_promoted_members(client, mlmr, db_session_factory):
    """The actual fix (OPEN_ITEMS.md section 1's MLModelCoordinationGroup
    x SA SMOS convergence item): report_performance used to compute
    groupRetrainTriggered and stop — nothing ever fired a retrain on a
    member model.
    """
    member_ids = _make_group_with_subscription(mlmr, db_session_factory, [ModelLifecycleState.PROMOTED, ModelLifecycleState.PROMOTED])
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
        lifecycle = client.get(f"/models/{member_id}/lifecycle").json()
        assert lifecycle["modelLifecycleState"] == ModelLifecycleState.TRAINING
        assert lifecycle["trainingJobId"] is not None


def test_group_retrain_trigger_skips_non_promoted_members(client, mlmr, db_session_factory):
    """CREATE_TRAINING is only a legal transition from PROMOTED (or
    REGISTERED/FAILED/TRAINING) — a member mid certification (never yet
    PROMOTED) must be skipped, not forced.
    """
    member_ids = _make_group_with_subscription(mlmr, db_session_factory, [ModelLifecycleState.PROMOTED, ModelLifecycleState.CERTIFIED])
    with db_session_factory() as session:
        sub = MLMFSubscription(model_id=member_ids[0], metric_types=["accuracy"], dme_type_id=uuid.uuid4(), guard_kpi_floor={"accuracy": 0.9})
        session.add(sub)
        session.commit()
        sub_id = sub.subscription_id

    resp = client.post(f"/mlmf/subscriptions/{sub_id}/reports", json={"accuracy": 0.5})
    body = resp.json()
    assert body["retrainedModelIds"] == [str(member_ids[0])]

    untouched = client.get(f"/models/{member_ids[1]}/lifecycle").json()
    assert untouched["modelLifecycleState"] == ModelLifecycleState.CERTIFIED
    assert untouched["trainingJobId"] is None


def test_no_group_retrain_when_not_breached(client, mlmr, db_session_factory):
    member_ids = _make_group_with_subscription(mlmr, db_session_factory, [ModelLifecycleState.PROMOTED])
    with db_session_factory() as session:
        sub = MLMFSubscription(model_id=member_ids[0], metric_types=["accuracy"], dme_type_id=uuid.uuid4(), guard_kpi_floor={"accuracy": 0.9})
        session.add(sub)
        session.commit()
        sub_id = sub.subscription_id

    resp = client.post(f"/mlmf/subscriptions/{sub_id}/reports", json={"accuracy": 0.99})
    body = resp.json()
    assert body["breachedFloor"] is False
    assert "groupRetrainTriggered" not in body


# ---------------------------------------------------------------- Validation / Emulation

def test_request_validation_requires_trained_model(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    resp = client.post("/validation-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "MODEL_NOT_CERTIFIED"


def test_request_validation_advances_to_validating_then_complete_to_validated(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.TRAINED)

    resp = client.post("/validation-jobs", json={"modelId": str(model_id), "producerId": "rapp-1", "validationCriteria": {"minAccuracy": 0.8}})
    assert resp.status_code == 201
    job_id = resp.json()["validationJobId"]
    assert client.get(f"/models/{model_id}/lifecycle").json()["modelLifecycleState"] == ModelLifecycleState.VALIDATING

    complete = client.post(f"/validation-jobs/{job_id}/complete", json={"succeeded": True, "metrics": {"accuracy": 0.95}})
    assert complete.status_code == 200
    assert complete.json()["status"] == "COMPLETED"
    assert client.get(f"/models/{model_id}/lifecycle").json()["modelLifecycleState"] == ModelLifecycleState.VALIDATED


def test_validation_failure_routes_the_model_to_failed(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.TRAINED)
    job_id = client.post("/validation-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()["validationJobId"]

    complete = client.post(f"/validation-jobs/{job_id}/complete", json={"succeeded": False})
    assert complete.json()["status"] == "FAILED"
    assert client.get(f"/models/{model_id}/lifecycle").json()["modelLifecycleState"] == ModelLifecycleState.FAILED


def test_list_validation_jobs_filters_by_model(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.TRAINED)
    job_id = client.post("/validation-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()["validationJobId"]

    listed = client.get("/validation-jobs", params={"model_id": str(model_id)}).json()
    assert [j["validationJobId"] for j in listed["items"]] == [job_id]
    assert listed["total"] == 1


def test_request_emulation_requires_validated_model_and_completes_to_emulated(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.TRAINED)
    rejected = client.post("/emulation-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"})
    assert rejected.status_code == 409

    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.VALIDATED)
    job_id = client.post("/emulation-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()["emulationJobId"]
    assert client.get(f"/models/{model_id}/lifecycle").json()["modelLifecycleState"] == ModelLifecycleState.EMULATING

    complete = client.post(f"/emulation-jobs/{job_id}/complete", json={"succeeded": True, "metrics": {"latencyMs": 12}})
    assert complete.json()["status"] == "COMPLETED"
    assert client.get(f"/models/{model_id}/lifecycle").json()["modelLifecycleState"] == ModelLifecycleState.EMULATED


def test_list_emulation_jobs_filters_by_model(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.VALIDATED)
    job_id = client.post("/emulation-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()["emulationJobId"]

    listed = client.get("/emulation-jobs", params={"model_id": str(model_id)}).json()
    assert [j["emulationJobId"] for j in listed["items"]] == [job_id]
    assert listed["total"] == 1


# ---------------------------------------------------------------- ModelLifecycle: advance + governance

def test_advance_model_lifecycle_fires_the_requested_event(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.TRAINING)
    resp = client.post(f"/models/{model_id}/advance", params={"event": "TRAINING_COMPLETE"})
    assert resp.status_code == 200
    assert resp.json()["modelLifecycleState"] == ModelLifecycleState.TRAINED


def test_advance_model_lifecycle_for_unknown_model_is_404(client, mlmr):
    resp = client.post(f"/models/{uuid.uuid4()}/advance", params={"event": "CERTIFY"})
    assert resp.status_code == 404


def test_advance_model_lifecycle_illegal_transition_is_a_clean_409(client, mlmr):
    model_id = mlmr.add_model()
    resp = client.post(f"/models/{model_id}/advance", params={"event": "TRAINING_COMPLETE"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "LIFECYCLE_ILLEGAL_TRANSITION"


def test_governance_decision_requires_decided_by(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.EMULATED)
    resp = client.post(f"/models/{model_id}/advance", params={"event": "SUBMIT_FOR_APPROVAL"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "GOVERNANCE_DECIDER_REQUIRED"


def test_full_governance_pipeline_writes_a_certification_record_per_decision(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.EMULATED)

    for event, expected_state in [
        ("SUBMIT_FOR_APPROVAL", ModelLifecycleState.PENDING_APPROVAL),
        ("APPROVE", ModelLifecycleState.APPROVED),
        ("CERTIFY", ModelLifecycleState.CERTIFIED),
        ("PROMOTE", ModelLifecycleState.PROMOTED),
    ]:
        resp = client.post(f"/models/{model_id}/advance", params={"event": event, "decided_by": "operator-1", "rationale": f"{event} looks good"})
        assert resp.status_code == 200
        assert resp.json()["modelLifecycleState"] == expected_state

    history = client.get(f"/models/{model_id}/governance-history").json()["items"]
    assert [h["decision"] for h in history] == ["SUBMIT_FOR_APPROVAL", "APPROVE", "CERTIFY", "PROMOTE"]
    assert all(h["decidedBy"] == "operator-1" for h in history)


def test_rollback_demotes_a_promoted_model_and_is_itself_recorded(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.PROMOTED)

    resp = client.post(f"/models/{model_id}/advance", params={"event": "ROLLBACK", "decided_by": "operator-1", "rationale": "regression found"})
    assert resp.status_code == 200
    assert resp.json()["modelLifecycleState"] == ModelLifecycleState.CERTIFIED

    history = client.get(f"/models/{model_id}/governance-history").json()["items"]
    assert history[-1] == {
        "certificationRecordId": history[-1]["certificationRecordId"], "modelId": str(model_id),
        "decision": "ROLLBACK", "decidedBy": "operator-1", "rationale": "regression found",
        "decidedAt": history[-1]["decidedAt"],
    }


def test_deprecate_and_retire_do_not_require_decided_by(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.CERTIFIED)

    assert client.post(f"/models/{model_id}/advance", params={"event": "DEPRECATE"}).json()["modelLifecycleState"] == ModelLifecycleState.DEPRECATED
    assert client.post(f"/models/{model_id}/advance", params={"event": "RETIRE"}).json()["modelLifecycleState"] == ModelLifecycleState.RETIRED


def test_lifecycle_history_records_every_transition(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.TRAINING)
    client.post(f"/models/{model_id}/advance", params={"event": "TRAINING_COMPLETE"})

    history = client.get(f"/models/{model_id}/lifecycle-history").json()["items"]
    assert history == [{"fsm": "MODEL", "fromState": "TRAINING", "toState": "TRAINED", "event": "TRAINING_COMPLETE", "occurredAt": history[0]["occurredAt"]}]


def test_list_model_lifecycles_returns_every_touched_model(client, mlmr, db_session_factory):
    """(GUI) The Models table's own State/Node-groups columns need every
    model's lifecycle in one call, not one fetch per row.
    """
    model_a = mlmr.add_model()
    model_b = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_a, model_lifecycle_state=ModelLifecycleState.CERTIFIED)
    # model_b never touched — absent from the list, same as before any AIMgF interaction

    listed = client.get("/model-lifecycles").json()["items"]
    assert [l["modelId"] for l in listed] == [str(model_a)]
    assert listed[0]["modelLifecycleState"] == ModelLifecycleState.CERTIFIED


def test_lifecycle_endpoint_lazily_creates_a_registered_row(client, mlmr):
    """A model AIMgF has never been asked to act on before still answers
    something sensible — REGISTERED/NOT_DEPLOYED, not a 404 or 500.
    """
    model_id = mlmr.add_model()
    resp = client.get(f"/models/{model_id}/lifecycle")
    assert resp.status_code == 200
    assert resp.json()["modelLifecycleState"] == ModelLifecycleState.REGISTERED
    assert resp.json()["runtimeLifecycleState"] == RuntimeLifecycleState.NOT_DEPLOYED


# ---------------------------------------------------------------- RuntimeLifecycle (jointly with NFO)

def test_deploy_runtime_requires_certified_or_promoted(client, mlmr):
    model_id = mlmr.add_model()
    resp = client.post(f"/models/{model_id}/runtime/deploy")
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "MODEL_NOT_CERTIFIED"
    assert mlmr.nfo.descriptors == {}  # never touches NFO when the guard fails


def test_deploy_runtime_calls_nfo_and_records_its_ids(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.CERTIFIED)

    resp = client.post(f"/models/{model_id}/runtime/deploy")
    assert resp.status_code == 201
    body = resp.json()
    assert body["runtimeLifecycleState"] == RuntimeLifecycleState.DEPLOYED
    assert body["nfDeploymentDescriptorId"] is not None
    assert body["nfDeploymentId"] is not None

    assert len(mlmr.nfo.descriptors) == 1
    assert len(mlmr.nfo.deployments) == 1
    # a model runtime has no onboarded ApplicationPackage behind it
    assert list(mlmr.nfo.descriptors.values())[0]["packageId"] is None


def test_deploy_runtime_twice_is_rejected_before_touching_nfo_again(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.CERTIFIED)
    client.post(f"/models/{model_id}/runtime/deploy")

    resp = client.post(f"/models/{model_id}/runtime/deploy")
    assert resp.status_code == 409
    assert len(mlmr.nfo.descriptors) == 1  # unchanged — the FSM guard fired before any second NFO call


def test_activate_scale_and_terminate_runtime_pipeline(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.CERTIFIED)
    client.post(f"/models/{model_id}/runtime/deploy")

    activated = client.post(f"/models/{model_id}/runtime/activate")
    assert activated.json()["runtimeLifecycleState"] == RuntimeLifecycleState.ACTIVE

    scaled = client.post(f"/models/{model_id}/runtime/scale")
    assert scaled.status_code == 200
    assert scaled.json()["runtimeLifecycleState"] == RuntimeLifecycleState.ACTIVE
    deployment_id = scaled.json()["nfDeploymentId"]
    assert mlmr.nfo.deployments[deployment_id]["scaled"] == 1

    terminated = client.post(f"/models/{model_id}/runtime/terminate")
    assert terminated.json()["runtimeLifecycleState"] == RuntimeLifecycleState.TERMINATED
    assert deployment_id not in mlmr.nfo.deployments


def test_update_node_groups_is_independent_of_deploy(client, mlmr):
    """Called by MLLF's own request_model_deployment — the node-group
    write itself doesn't require a runtime to already be deployed.
    """
    model_id = mlmr.add_model()
    resp = client.patch(f"/models/{model_id}/runtime/node-groups", json={"clearedNodeGroups": ["ng1", "ng2"]})
    assert resp.status_code == 200
    assert resp.json()["clearedNodeGroups"] == ["ng1", "ng2"]


# ---------------------------------------------------------------- Inference

def test_request_inference_requires_active_runtime(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.CERTIFIED)
    resp = client.post(f"/models/{model_id}/inference-jobs")
    assert resp.status_code == 409


def test_request_inference_on_active_runtime_creates_a_running_job(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, runtime_lifecycle_state=RuntimeLifecycleState.ACTIVE)
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
    model_a = mlmr.add_model()
    model_b = mlmr.add_model()
    job_a = client.post("/training-jobs", json={"modelId": str(model_a), "producerId": "rapp-1"}).json()["trainingJobId"]
    client.post("/training-jobs", json={"modelId": str(model_b), "producerId": "rapp-1"})

    assert client.get("/training-jobs").json()["total"] == 2
    only_a = client.get("/training-jobs", params={"model_id": str(model_a)}).json()["items"]
    assert [j["trainingJobId"] for j in only_a] == [job_a]
    assert only_a[0]["status"] == "IN_PROGRESS" and only_a[0]["modelId"] == str(model_a)

    client.delete(f"/training-jobs/{job_a}")
    assert [j["trainingJobId"] for j in client.get("/training-jobs", params={"status": "CANCELLED"}).json()["items"]] == [job_a]


def test_list_inference_jobs_filters_by_model(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, runtime_lifecycle_state=RuntimeLifecycleState.ACTIVE)
    job_id = client.post(f"/models/{model_id}/inference-jobs").json()["inferenceJobId"]

    listed = client.get("/inference-jobs", params={"model_id": str(model_id)}).json()["items"]
    assert listed == [{"inferenceJobId": job_id, "modelId": str(model_id), "status": "RUNNING", "notificationDestination": None}]
    assert client.get("/inference-jobs", params={"status": "COMPLETED"}).json()["items"] == []


def test_list_mlmf_subscriptions_and_their_reports_newest_first(client, mlmr):
    """GUI pass: MLMF subscriptions/reports were write-only."""
    model_id = mlmr.add_model()
    sub_id = client.post("/mlmf/subscriptions", params={"model_id": str(model_id), "dme_type_id": str(uuid.uuid4())},
                          json={"metric_types": ["accuracy"], "guard_kpi_floor": {"accuracy": 0.9}}).json()["subscriptionId"]
    client.post(f"/mlmf/subscriptions/{sub_id}/reports", json={"accuracy": 0.95})
    client.post(f"/mlmf/subscriptions/{sub_id}/reports", json={"accuracy": 0.5})

    subs = client.get("/mlmf/subscriptions", params={"model_id": str(model_id)}).json()["items"]
    assert [(s["subscriptionId"], s["guardKpiFloor"]) for s in subs] == [(sub_id, {"accuracy": 0.9})]

    reports = client.get(f"/mlmf/subscriptions/{sub_id}/reports").json()["items"]
    assert [(r["metrics"]["accuracy"], r["breachedFloor"]) for r in reports] == [(0.5, True), (0.95, False)]

    breached = client.get("/mlmf/reports", params={"breached_only": True}).json()["items"]
    assert [r["metrics"]["accuracy"] for r in breached] == [0.5]
    assert client.get("/mlmf/reports").json()["total"] == 2


def test_list_mlmf_reports_404_on_an_unknown_subscription(client):
    assert client.get(f"/mlmf/subscriptions/{uuid.uuid4()}/reports").status_code == 404


def test_subscribe_performance_monitoring_round_trips_notification_destination(client, mlmr):
    """SPEC_AUDIT.md's `MLMFSubscription` finding, closed: every other
    subscription-shaped resource in this build notifies a real
    notification_destination — this one previously had no such field.
    """
    model_id = mlmr.add_model()
    sub_id = client.post("/mlmf/subscriptions", params={
        "model_id": str(model_id), "dme_type_id": str(uuid.uuid4()), "notification_destination": "http://consumer/mlmf-events",
    }, json={"metric_types": ["accuracy"], "guard_kpi_floor": {"accuracy": 0.9}}).json()["subscriptionId"]

    subs = client.get("/mlmf/subscriptions", params={"model_id": str(model_id)}).json()["items"]
    assert subs[0]["notificationDestination"] == "http://consumer/mlmf-events"


def test_subscribe_performance_monitoring_without_notification_destination_is_still_legal(client, mlmr):
    """A purely poll-based consumer may still omit it, same permissive
    shape as every other subscription-shaped resource in this build.
    """
    model_id = mlmr.add_model()
    sub_id = client.post("/mlmf/subscriptions", params={"model_id": str(model_id), "dme_type_id": str(uuid.uuid4())},
                          json={"metric_types": ["accuracy"], "guard_kpi_floor": None}).json()["subscriptionId"]
    subs = client.get("/mlmf/subscriptions", params={"model_id": str(model_id)}).json()["items"]
    assert subs[0]["notificationDestination"] is None


def test_report_performance_notifies_the_subscribers_own_destination(client, mlmr, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    model_id = mlmr.add_model()
    sub_id = client.post("/mlmf/subscriptions", params={
        "model_id": str(model_id), "dme_type_id": str(uuid.uuid4()), "notification_destination": "http://consumer/mlmf-events",
    }, json={"metric_types": ["accuracy"], "guard_kpi_floor": {"accuracy": 0.9}}).json()["subscriptionId"]

    resp = client.post(f"/mlmf/subscriptions/{sub_id}/reports", json={"accuracy": 0.5})
    assert resp.status_code == 200

    assert len(calls) == 1
    url, payload = calls[0]
    assert url == "http://consumer/mlmf-events"
    assert payload["modelId"] == str(model_id)
    assert payload["metrics"] == {"accuracy": 0.5}
    assert payload["breachedFloor"] is True


def test_report_performance_skips_notification_when_no_destination_registered(client, mlmr, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    model_id = mlmr.add_model()
    sub_id = client.post("/mlmf/subscriptions", params={"model_id": str(model_id), "dme_type_id": str(uuid.uuid4())},
                          json={"metric_types": ["accuracy"], "guard_kpi_floor": None}).json()["subscriptionId"]

    resp = client.post(f"/mlmf/subscriptions/{sub_id}/reports", json={"accuracy": 0.5})
    assert resp.status_code == 200
    assert calls == []


def test_report_performance_succeeds_even_if_the_subscriber_is_unreachable(client, mlmr, monkeypatch):
    import httpx as httpx_module

    def raise_error(url, json=None, timeout=None):
        raise httpx_module.ConnectError("unreachable")

    monkeypatch.setattr("app.main.httpx.post", raise_error)

    model_id = mlmr.add_model()
    sub_id = client.post("/mlmf/subscriptions", params={
        "model_id": str(model_id), "dme_type_id": str(uuid.uuid4()), "notification_destination": "http://consumer/mlmf-events",
    }, json={"metric_types": ["accuracy"], "guard_kpi_floor": None}).json()["subscriptionId"]

    resp = client.post(f"/mlmf/subscriptions/{sub_id}/reports", json={"accuracy": 0.5})
    assert resp.status_code == 200  # must not raise despite the unreachable subscriber


def test_unsubscribe_performance_monitoring(client, mlmr):
    """SPEC_AUDIT.md's `MLMFSubscription` finding, closed: previously
    this subscription could only be created and read, never torn down.
    """
    model_id = mlmr.add_model()
    sub_id = client.post("/mlmf/subscriptions", params={"model_id": str(model_id), "dme_type_id": str(uuid.uuid4())},
                          json={"metric_types": ["accuracy"], "guard_kpi_floor": None}).json()["subscriptionId"]

    resp = client.delete(f"/mlmf/subscriptions/{sub_id}")
    assert resp.status_code == 204

    subs = client.get("/mlmf/subscriptions", params={"model_id": str(model_id)}).json()["items"]
    assert subs == []


def test_unsubscribe_unknown_performance_monitoring_is_idempotent(client):
    resp = client.delete(f"/mlmf/subscriptions/{uuid.uuid4()}")
    assert resp.status_code == 204


def test_report_performance_against_unknown_subscription_is_a_clean_404(client):
    """`docs/call-flows/13-mlmf-subscription-lifecycle.md`'s own gap,
    closed: this used to raise an unhandled AttributeError (a bare 500)
    reading `sub.guard_kpi_floor` with no null-check.
    """
    resp = client.post(f"/mlmf/subscriptions/{uuid.uuid4()}/reports", json={"accuracy": 0.5})
    assert resp.status_code == 404
    assert resp.json()["detail"]["title"] == "MLMF_SUBSCRIPTION_NOT_FOUND"


def test_report_performance_after_unsubscribe_is_a_clean_404(client, mlmr):
    model_id = mlmr.add_model()
    sub_id = client.post("/mlmf/subscriptions", params={"model_id": str(model_id), "dme_type_id": str(uuid.uuid4())},
                          json={"metric_types": ["accuracy"], "guard_kpi_floor": None}).json()["subscriptionId"]
    client.delete(f"/mlmf/subscriptions/{sub_id}")

    resp = client.post(f"/mlmf/subscriptions/{sub_id}/reports", json={"accuracy": 0.5})
    assert resp.status_code == 404
    assert resp.json()["detail"]["title"] == "MLMF_SUBSCRIPTION_NOT_FOUND"


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
    names = {g["featureGroupName"] for g in resp.json()["items"]}
    assert names == {"cellCounters", "handoverCounters"}


def test_list_feature_groups_returns_empty_list_when_none_registered(client):
    resp = client.get("/feature-groups")
    assert resp.json() == {"items": [], "total": 0, "limit": 100, "offset": 0}


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
