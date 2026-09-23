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
from app.models import AIMLModel, MLMFSubscription, MLModelCoordinationGroup, PerformanceReport, TrainingJob
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
        MLMFSubscription.__table__, PerformanceReport.__table__,
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
        for member_id, state in zip(member_ids, member_states):
            session.add(AIMLModel(model_id=member_id, registration_id=str(uuid.uuid4()), model_type="t", version="1.0", state=state))
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


def test_get_unknown_model_is_404(client):
    resp = client.get(f"/models/{uuid.uuid4()}")
    assert resp.status_code == 404
