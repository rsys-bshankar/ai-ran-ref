"""Tests for Wave 7 (docs/ROADMAP.md W7-03/W7-04): per-mode
runtime profiles carried into NFO descriptors, and execution timeouts.
Run with: pytest smo/aimgf/tests -q
"""

import datetime
import uuid

import pytest

from test_main import FakeResponse, _set_lifecycle, client, db_session_factory, mlmr  # noqa: F401  (pytest fixtures)

from app.models import EmulationJob, InferenceJob, TrainingJob, ValidationJob
from app.statemachine import ModelLifecycleState, RuntimeLifecycleState

PROFILES = {"TRAINING": {"cpu": 8, "memory": "16Gi", "gpu": 0}, "VALIDATION": {"cpu": 4, "memory": "8Gi", "gpu": 0},
            "EMULATION": {"cpu": 4, "memory": "8Gi", "gpu": 0}, "INFERENCE": {"cpu": 2, "memory": "4Gi", "gpu": 0}}


@pytest.fixture
def package(mlmr, monkeypatch):
    """An onboarded rApp package whose manifest declares runtimeProfiles,
    served from Onboarding's onboarding-status route; every other GET still
    goes to the MLMR double."""
    package_id = uuid.uuid4()

    def fake_get(self, path, **kw):
        if path == f"/onboarding/packages/{package_id}/onboarding-status":
            return FakeResponse(200, {"packageId": str(package_id), "aiCapabilities": {"runtimeProfiles": PROFILES}})
        if path.startswith("/onboarding/packages/"):
            return FakeResponse(404, {"detail": {"title": "no such package"}})
        return mlmr.get(path, **kw)

    monkeypatch.setattr("app.main.R1Client.get", fake_get)
    return package_id


def _descriptor_resources(mlmr):
    return [d["workloadTemplate"].get("resources") for d in mlmr.nfo.descriptors.values()]


# ---------------------------------------------------------------- W7-03 runtime profiles

def test_training_runtime_is_sized_from_the_package_profile(client, mlmr, package):
    model_id = mlmr.add_model()
    job_id = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "es-rapp",
                                                 "packageId": str(package)}).json()["trainingJobId"]
    assert _descriptor_resources(mlmr) == [PROFILES["TRAINING"]]
    status = client.get(f"/training-jobs/{job_id}/status").json()
    assert status["runtimeProfile"] == PROFILES["TRAINING"]
    assert status["timeoutSeconds"] == 1800


def test_explicit_profile_overrides_the_package(client, mlmr, package):
    model_id = mlmr.add_model()
    client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "p", "packageId": str(package),
                                        "runtimeProfile": {"cpu": 1, "memory": "1Gi"}})
    assert _descriptor_resources(mlmr) == [{"cpu": 1, "memory": "1Gi"}]
    assert client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "p",
                                               "runtimeProfile": {"cpu": -1}}).status_code == 422


def test_unknown_package_is_404_and_no_package_means_unsized(client, mlmr, package):
    model_id = mlmr.add_model()
    resp = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "p", "packageId": str(uuid.uuid4())})
    assert resp.status_code == 404 and resp.json()["detail"]["title"] == "PACKAGE_NOT_FOUND"
    client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "p"})
    assert _descriptor_resources(mlmr) == [None]


def test_validation_emulation_and_inference_runtimes_use_their_own_modes(client, mlmr, package, db_session_factory):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.TRAINED, training_approved=True)
    client.post("/validation-jobs", json={"modelId": str(model_id), "producerId": "p", "packageId": str(package)})
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.VALIDATED, validation_approved=True)
    client.post("/emulation-jobs", json={"modelId": str(model_id), "producerId": "p", "packageId": str(package)})
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.CERTIFIED)
    deployed = client.post(f"/models/{model_id}/runtime/deploy", params={"package_id": str(package)}).json()
    assert _descriptor_resources(mlmr) == [PROFILES["VALIDATION"], PROFILES["EMULATION"], PROFILES["INFERENCE"]]
    assert deployed["runtimeProfile"] == PROFILES["INFERENCE"]


# ---------------------------------------------------------------- W7-04 timeouts

def _backdate(db_session_factory, cls, job_id, seconds):
    with db_session_factory() as session:
        job = session.get(cls, job_id)
        job.started_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=seconds)
        session.commit()


def test_training_timeout_fails_run_and_model_without_corruption(client, mlmr, db_session_factory, monkeypatch):
    notified = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: notified.append((url, json)))
    model_id = mlmr.add_model()
    job_id = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "p", "timeoutSeconds": 60,
                                                 "notificationUri": "http://rapp/done"}).json()["trainingJobId"]
    _backdate(db_session_factory, TrainingJob, uuid.UUID(job_id), 61)

    swept = client.post("/execution-timeouts/sweep").json()
    assert swept["expired"] == [{"jobKind": "TRAINING", "jobId": job_id}]
    assert swept["defaultTimeoutSeconds"] == {"TRAINING": 1800, "VALIDATION": 900, "EMULATION": 1800, "INFERENCE": 5}
    assert client.get(f"/training-jobs/{job_id}/status").json()["status"] == "FAILED"
    assert client.get(f"/models/{model_id}/lifecycle").json()["modelLifecycleState"] == ModelLifecycleState.FAILED
    assert mlmr.nfo.deployments == {}  # runtime torn down
    process = client.get("/ml-training-processes").json()["items"][0]["attributes"]["progressStatus"]
    assert (process["status"], process["resultStateInfo"]) == ("FAILED", "TIMEOUT")
    assert notified == [("http://rapp/done", {"jobKind": "TRAINING", "jobId": job_id, "succeeded": False,
                                              "outcomeArtifactDmeTypeId": None, "metrics": {"failureReason": "TIMEOUT"}})]
    # a late completion can't resurrect it; the model can be retrained (FAILED is the retry point)
    assert client.post(f"/training-jobs/{job_id}/complete", json={"succeeded": True}).status_code == 409
    assert client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "p"}).status_code == 201


def test_reads_expire_lazily_and_suspended_runs_pause(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    job_id = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "p", "timeoutSeconds": 60}).json()["trainingJobId"]
    client.post(f"/training-jobs/{job_id}/suspend")
    _backdate(db_session_factory, TrainingJob, uuid.UUID(job_id), 600)
    assert client.get(f"/training-jobs/{job_id}/status").json()["status"] == "SUSPENDED"
    client.post(f"/training-jobs/{job_id}/resume")  # clock restarts
    assert client.get(f"/training-jobs/{job_id}/status").json()["status"] == "IN_PROGRESS"
    _backdate(db_session_factory, TrainingJob, uuid.UUID(job_id), 61)
    assert client.get("/training-jobs").json()["items"][0]["status"] == "FAILED"  # list read sweeps too


def test_validation_and_emulation_timeouts(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.TRAINED, training_approved=True)
    v_id = client.post("/validation-jobs", json={"modelId": str(model_id), "producerId": "p"}).json()["validationJobId"]
    assert client.get(f"/validation-jobs/{v_id}/status").json()["timeoutSeconds"] == 900
    _backdate(db_session_factory, ValidationJob, uuid.UUID(v_id), 901)
    status = client.get(f"/validation-jobs/{v_id}/status").json()
    assert (status["status"], status["metrics"]) == ("FAILED", {"failureReason": "TIMEOUT"})
    assert client.get(f"/models/{model_id}/lifecycle").json()["modelLifecycleState"] == ModelLifecycleState.FAILED
    assert client.get("/ml-testing-reports").json()["items"][0]["attributes"]["mLTestingResult"] == "FAILED"

    other = mlmr.add_model()
    _set_lifecycle(db_session_factory, other, model_lifecycle_state=ModelLifecycleState.VALIDATED, validation_approved=True)
    e_id = client.post("/emulation-jobs", json={"modelId": str(other), "producerId": "p", "timeoutSeconds": 5}).json()["emulationJobId"]
    _backdate(db_session_factory, EmulationJob, uuid.UUID(e_id), 6)
    assert client.post(f"/emulation-jobs/{e_id}/complete", json={"succeeded": True}).status_code == 409
    assert client.get(f"/models/{other}/lifecycle").json()["modelLifecycleState"] == ModelLifecycleState.FAILED


def test_timeout_never_forces_an_illegal_lifecycle_transition(client, mlmr, db_session_factory):
    """The model moved on (e.g. an operator reset it) — the expired run
    fails, the model's lifecycle is left alone."""
    model_id = mlmr.add_model()
    job_id = client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "p", "timeoutSeconds": 1}).json()["trainingJobId"]
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.PROMOTED)
    _backdate(db_session_factory, TrainingJob, uuid.UUID(job_id), 2)
    assert client.post("/execution-timeouts/sweep").json()["expired"][0]["jobId"] == job_id
    assert client.get(f"/models/{model_id}/lifecycle").json()["modelLifecycleState"] == ModelLifecycleState.PROMOTED


def test_inference_five_second_default_and_late_resolve(client, mlmr, db_session_factory, monkeypatch):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.CERTIFIED,
                   runtime_lifecycle_state=RuntimeLifecycleState.ACTIVE)
    job_id = client.post(f"/models/{model_id}/inference-jobs").json()["inferenceJobId"]
    assert client.get(f"/inference-jobs/{job_id}/status").json()["timeoutSeconds"] == 5
    _backdate(db_session_factory, InferenceJob, uuid.UUID(job_id), 6)
    assert client.get(f"/inference-jobs/{job_id}/status").json()["status"] == "FAILED"
    assert client.post(f"/inference-jobs/{job_id}/resolve", params={"succeeded": True}).status_code == 409
    assert client.get(f"/inference-jobs/{uuid.uuid4()}/status").status_code == 404

    monkeypatch.setenv("AIMGF_TIMEOUT_INFERENCE_SECONDS", "30")
    job_id = client.post(f"/models/{model_id}/inference-jobs").json()["inferenceJobId"]
    assert client.get(f"/inference-jobs/{job_id}/status").json()["timeoutSeconds"] == 30


# ---------------------------------------------------------------- OI-2-training-lifecycle-edges: NRM resume, sweep, sizing

def _started_at(db_session_factory, cls, job_id):
    with db_session_factory() as session:
        return session.get(cls, uuid.UUID(job_id)).started_at


@pytest.mark.parametrize("via", ["request", "process"])
def test_nrm_training_resume_restarts_the_timeout_clock(client, mlmr, db_session_factory, via):
    model_id = mlmr.add_model()
    request_id = client.post("/ml-training-requests", json={"mLModelRef": str(model_id), "trainingRequestSource": "x",
                                                           "timeoutSeconds": 60}).json()["id"]
    process_id = client.get("/ml-training-processes").json()["items"][0]["id"]
    client.patch(f"/ml-training-requests/{request_id}", json={"suspendRequest": True})
    _backdate(db_session_factory, TrainingJob, uuid.UUID(request_id), 600)
    if via == "request":
        client.patch(f"/ml-training-requests/{request_id}", json={"suspendRequest": False})
    else:
        client.patch(f"/ml-training-processes/{process_id}", json={"suspendProcess": False})
    # resumed with a fresh clock — not instantly expired by the 600 s spent suspended
    assert client.get(f"/training-jobs/{request_id}/status").json()["status"] == "IN_PROGRESS"
    _backdate(db_session_factory, TrainingJob, uuid.UUID(request_id), 61)
    assert client.get(f"/ml-training-requests/{request_id}").json()["attributes"]["requestStatus"] == "FAILED"


def test_nrm_testing_resume_restarts_the_timeout_clock(client, mlmr, db_session_factory):
    model_id = mlmr.add_model()
    _set_lifecycle(db_session_factory, model_id, model_lifecycle_state=ModelLifecycleState.TRAINED, training_approved=True)
    request_id = client.post("/ml-testing-requests", json={"mLModelRef": str(model_id), "timeoutSeconds": 60}).json()["id"]
    client.patch(f"/ml-testing-requests/{request_id}", json={"suspendRequest": True})
    _backdate(db_session_factory, ValidationJob, uuid.UUID(request_id), 600)
    client.patch(f"/ml-testing-requests/{request_id}", json={"suspendRequest": False})
    assert client.get(f"/validation-jobs/{request_id}/status").json()["status"] == "RUNNING"
    _backdate(db_session_factory, ValidationJob, uuid.UUID(request_id), 61)
    # the NRM read itself sweeps
    assert client.get(f"/ml-testing-requests/{request_id}").json()["attributes"]["requestStatus"] == "FINISHED"
    assert client.get(f"/models/{model_id}/lifecycle").json()["modelLifecycleState"] == ModelLifecycleState.FAILED


@pytest.mark.parametrize("path", ["/ml-training-requests", "/ml-training-requests/{id}", "/ml-training-processes",
                                  "/ml-training-processes/{pid}"])
def test_nrm_training_reads_sweep_overdue_runs(client, mlmr, db_session_factory, path):
    model_id = mlmr.add_model()
    request_id = client.post("/ml-training-requests", json={"mLModelRef": str(model_id), "trainingRequestSource": "x",
                                                           "timeoutSeconds": 60}).json()["id"]
    process_id = client.get("/ml-training-processes").json()["items"][0]["id"]
    _backdate(db_session_factory, TrainingJob, uuid.UUID(request_id), 61)
    client.get(path.format(id=request_id, pid=process_id))
    with db_session_factory() as session:
        assert session.get(TrainingJob, uuid.UUID(request_id)).status == "FAILED"
    assert client.get(f"/models/{model_id}/lifecycle").json()["modelLifecycleState"] == ModelLifecycleState.FAILED


def test_nrm_requests_take_a_runtime_profile_and_timeout(client, mlmr, package, db_session_factory):
    model_id = mlmr.add_model()
    request_id = client.post("/ml-training-requests", json={"mLModelRef": str(model_id), "trainingRequestSource": "x",
                                                           "packageId": str(package), "timeoutSeconds": 120}).json()["id"]
    status = client.get(f"/training-jobs/{request_id}/status").json()
    assert (status["runtimeProfile"], status["timeoutSeconds"]) == (PROFILES["TRAINING"], 120)

    other = mlmr.add_model()
    _set_lifecycle(db_session_factory, other, model_lifecycle_state=ModelLifecycleState.TRAINED, training_approved=True)
    testing_id = client.post("/ml-testing-requests", json={"mLModelRef": str(other),
                                                          "runtimeProfile": {"cpu": 2, "memory": "2Gi"}}).json()["id"]
    status = client.get(f"/validation-jobs/{testing_id}/status").json()
    assert (status["runtimeProfile"], status["timeoutSeconds"]) == ({"cpu": 2, "memory": "2Gi"}, 900)
    assert _descriptor_resources(mlmr) == [PROFILES["TRAINING"], {"cpu": 2, "memory": "2Gi"}]
    assert client.post("/ml-training-requests", json={"mLModelRef": str(model_id), "trainingRequestSource": "x",
                                                      "timeoutSeconds": 0}).status_code == 422
