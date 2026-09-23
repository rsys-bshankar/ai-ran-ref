"""Tests for AI/ML Workflow's routes (AI/ML Workflow LLD sections 1, 4)
— RequestTraining's model-state-to-event mapping and its handling of an
already-in-flight TrainingJob, neither of which had route-level coverage
before (only the FSM itself, in test_statemachine.py).
Run with: pytest smo/ai-ml-workflow/tests -q
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from smo_shared.db import Base, get_session

from app.main import app
from app.models import AIMLModel, MLModelCoordinationGroup, TrainingJob
from app.statemachine import ModelState


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[AIMLModel.__table__, TrainingJob.__table__, MLModelCoordinationGroup.__table__])
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
