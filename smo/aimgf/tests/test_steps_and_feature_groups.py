"""OI-5-aiml-trainingjob-steps (a training run's step progress) and
OI-5-aiml-featuregroup-dme (an enable_dme feature group's DME data job).
Run with: pytest smo/aimgf/tests -q
"""

import uuid

import pytest

from test_main import FakeResponse, _feature_group_body, client, db_session_factory, mlmr  # noqa: F401  (pytest fixtures)


# ---------------------------------------------------------------- OI-5-aiml-trainingjob-steps

def _start(client, mlmr):
    model_id = mlmr.add_model()
    return client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()["trainingJobId"]


def _progress(client, job_id, step):
    return client.post(f"/training-jobs/{job_id}/progress", json={"step": step})


def _steps(client, job_id):
    body = client.get(f"/training-jobs/{job_id}/status").json()
    return body["currentStep"], body["steps"]


def test_a_new_run_is_extracting_data(client, mlmr):
    job_id = _start(client, mlmr)
    assert _steps(client, job_id) == ("DATA_EXTRACTION", {
        "DATA_EXTRACTION": "IN_PROGRESS", "TRAINING": "NOT_STARTED", "TRAINED_MODEL": "NOT_STARTED"})


def test_progress_moves_forward_and_finishes_earlier_steps(client, mlmr):
    job_id = _start(client, mlmr)
    resp = _progress(client, job_id, "TRAINING")
    assert resp.status_code == 200
    assert resp.json()["steps"] == {"DATA_EXTRACTION": "FINISHED", "TRAINING": "IN_PROGRESS", "TRAINED_MODEL": "NOT_STARTED"}
    assert _progress(client, job_id, "TRAINING").status_code == 200  # repeating the current step is a no-op
    assert _progress(client, job_id, "TRAINED_MODEL").json()["currentStep"] == "TRAINED_MODEL"
    listed = client.get("/training-jobs").json()["items"][0]
    assert listed["steps"]["TRAINING"] == "FINISHED" and listed["currentStep"] == "TRAINED_MODEL"


def test_progress_never_goes_back(client, mlmr):
    job_id = _start(client, mlmr)
    _progress(client, job_id, "TRAINING")
    resp = _progress(client, job_id, "DATA_EXTRACTION")
    assert resp.status_code == 409 and resp.json()["detail"]["title"] == "TRAINING_JOB_ILLEGAL_TRANSITION"


def test_a_suspended_or_ended_run_makes_no_progress(client, mlmr):
    job_id = _start(client, mlmr)
    client.post(f"/training-jobs/{job_id}/suspend")
    assert _steps(client, job_id)[1]["DATA_EXTRACTION"] == "SUSPENDED"
    assert _progress(client, job_id, "TRAINING").status_code == 409
    client.post(f"/training-jobs/{job_id}/resume")
    assert _progress(client, job_id, "TRAINING").status_code == 200
    client.delete(f"/training-jobs/{job_id}")
    assert _progress(client, job_id, "TRAINED_MODEL").status_code == 409


def test_how_the_run_ended_shows_on_the_step_it_reached(client, mlmr):
    failed = _start(client, mlmr)
    _progress(client, failed, "TRAINING")
    client.post(f"/training-jobs/{failed}/complete", json={"succeeded": False})
    assert _steps(client, failed)[1] == {"DATA_EXTRACTION": "FINISHED", "TRAINING": "FAILED", "TRAINED_MODEL": "NOT_STARTED"}

    cancelled = _start(client, mlmr)
    client.delete(f"/training-jobs/{cancelled}")
    assert _steps(client, cancelled)[1]["DATA_EXTRACTION"] == "CANCELLED"


def test_a_finished_run_finished_every_step(client, mlmr):
    """Completion is the runtime's report that the trained model exists,
    whichever step it last reported."""
    job_id = _start(client, mlmr)
    client.post(f"/training-jobs/{job_id}/complete", json={"succeeded": True})
    assert set(_steps(client, job_id)[1].values()) == {"FINISHED"}


def test_progress_validates_the_step_and_the_job(client, mlmr):
    job_id = _start(client, mlmr)
    assert _progress(client, job_id, "DEPLOYING").status_code == 422
    assert _progress(client, str(uuid.uuid4()), "TRAINING").status_code == 404


# ---------------------------------------------------------------- OI-5-aiml-featuregroup-dme

class FakeDme:
    def __init__(self, refuse=None):
        self.jobs: dict[str, dict] = {}
        self.refuse = refuse

    def post(self, path, json=None, **kw):
        assert path == "/dme/data-jobs", path
        if self.refuse:
            return FakeResponse(self.refuse[0], {"detail": {"title": self.refuse[1], "detail": "not offered"}})
        job_id = str(uuid.uuid4())
        self.jobs[job_id] = json
        return FakeResponse(202, {"dataJobId": job_id})

    def delete(self, path, **kw):
        self.jobs.pop(path.rsplit("/", 1)[-1], None)
        return FakeResponse(204, None)


@pytest.fixture
def dme(monkeypatch):
    fake = FakeDme()
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: fake.post(path, json=json, **kw))
    monkeypatch.setattr("app.main.R1Client.delete", lambda self, path, **kw: fake.delete(path, **kw))
    return fake


def test_an_enable_dme_group_creates_its_dme_data_job(client, dme):
    type_id = str(uuid.uuid4())
    resp = client.post("/feature-groups", json=_feature_group_body(
        enableDme=True, dmeTypeId=type_id, sourceName="gnb-du-01", measuredObjClass="NRCellDU"))

    assert resp.status_code == 201, resp.text
    body = resp.json()
    [(job_id, job)] = dme.jobs.items()
    assert body["dmeDataJobId"] == job_id and body["dmeTypeId"] == type_id
    assert (job["dmeTypeId"], job["dataDeliveryMode"], job["dataDeliveryMethod"], job["lifecycleStage"], job["consumerId"]) == \
        (type_id, "CONTINUOUS", "PULL_HTTP", "TRAINING", "aimgf:feature-group:cellCounters")
    assert job["productionJobDefinition"] == {"featureGroupName": "cellCounters", "features": ["throughput", "latency"],
                                              "measuredObjClass": "NRCellDU", "sourceName": "gnb-du-01",
                                              "measurement": "cell_kpis"}


def test_a_group_without_enable_dme_creates_no_job(client, dme):
    assert client.post("/feature-groups", json=_feature_group_body()).json()["dmeDataJobId"] is None
    assert dme.jobs == {}


def test_a_refused_dme_job_means_no_group(client, dme):
    dme.refuse = (409, "DELIVERY_METHOD_NOT_OFFERED")
    resp = client.post("/feature-groups", json=_feature_group_body(enableDme=True, dmeTypeId=str(uuid.uuid4()),
                                                                   dataDeliveryMethod="STREAMING_KAFKA"))
    assert resp.status_code == 422 and resp.json()["detail"]["title"] == "FEATURE_GROUP_DME_JOB_REFUSED"
    assert "DELIVERY_METHOD_NOT_OFFERED" in resp.json()["detail"]["detail"]
    assert client.get("/feature-groups").json()["total"] == 0


def test_a_duplicate_name_is_refused_before_any_job_is_created(client, dme):
    client.post("/feature-groups", json=_feature_group_body())
    resp = client.post("/feature-groups", json=_feature_group_body(enableDme=True, dmeTypeId=str(uuid.uuid4())))
    assert resp.status_code == 409 and dme.jobs == {}


def test_deleting_a_group_terminates_its_dme_job(client, dme):
    client.post("/feature-groups", json=_feature_group_body(enableDme=True, dmeTypeId=str(uuid.uuid4())))
    assert client.get("/feature-groups/cellCounters").json()["dmeDataJobId"] in dme.jobs

    resp = client.delete("/feature-groups/cellCounters")

    assert resp.json() == {"featureGroupName": "cellCounters", "dmeDataJobTeardown": "DONE"}
    assert dme.jobs == {}
    assert client.get("/feature-groups/cellCounters").status_code == 404
    assert client.delete("/feature-groups/cellCounters").json()["detail"]["title"] == "FEATURE_GROUP_NOT_FOUND"


def test_deleting_a_group_without_a_job_skips_the_teardown(client, dme):
    client.post("/feature-groups", json=_feature_group_body())
    assert client.delete("/feature-groups/cellCounters").json()["dmeDataJobTeardown"] == "SKIPPED"
