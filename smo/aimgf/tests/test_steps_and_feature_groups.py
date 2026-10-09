"""Tests of two routes added on top of the job routes: a training run's step progress (`POST /training-jobs/{id}/progress`, the derived `steps` of the status view;
OI-5-aiml-trainingjob-steps) and the DME data job of an `enableDme` feature group (OI-5-aiml-featuregroup-dme).

Fixtures and helpers come from `test_main.py` (`client`, `mlmr`, `db_session_factory`, `FakeResponse`, `_feature_group_body`), which this file imports so pytest treats
them as its own; the `dme` fixture below replaces the R1 `post` and `delete` with a recording DME double. SQLite, no network, no Postgres. Run with
`cd smo/aimgf && PYTHONPATH=.:../shared python -m pytest tests/test_steps_and_feature_groups.py -q`.
"""

import uuid

import pytest

from test_main import FakeResponse, _feature_group_body, client, db_session_factory, mlmr  # noqa: F401  (pytest fixtures)


# ---------------------------------------------------------------- OI-5-aiml-trainingjob-steps

def _start(client, mlmr):
    """Starts a training run for a fresh model through `POST /training-jobs` and returns the job id."""
    model_id = mlmr.add_model()
    return client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()["trainingJobId"]


def _progress(client, job_id, step):
    """Posts a step report for the run and returns the response, so a test can look at the status code as well as the body."""
    return client.post(f"/training-jobs/{job_id}/progress", json={"step": step})


def _steps(client, job_id):
    """Returns `(currentStep, steps)` from the run's status view."""
    body = client.get(f"/training-jobs/{job_id}/status").json()
    return body["currentStep"], body["steps"]


def test_a_new_run_is_extracting_data(client, mlmr):
    """A new run starts in DATA_EXTRACTION: that step is IN_PROGRESS and the later ones NOT_STARTED."""
    job_id = _start(client, mlmr)
    assert _steps(client, job_id) == ("DATA_EXTRACTION", {
        "DATA_EXTRACTION": "IN_PROGRESS", "TRAINING": "NOT_STARTED", "TRAINED_MODEL": "NOT_STARTED"})


def test_progress_moves_forward_and_finishes_earlier_steps(client, mlmr):
    """Reporting a later step marks the earlier steps FINISHED, repeating the current step is accepted as a no-op, and the list view carries the same steps."""
    job_id = _start(client, mlmr)
    resp = _progress(client, job_id, "TRAINING")
    assert resp.status_code == 200
    assert resp.json()["steps"] == {"DATA_EXTRACTION": "FINISHED", "TRAINING": "IN_PROGRESS", "TRAINED_MODEL": "NOT_STARTED"}
    assert _progress(client, job_id, "TRAINING").status_code == 200  # repeating the current step is a no-op
    assert _progress(client, job_id, "TRAINED_MODEL").json()["currentStep"] == "TRAINED_MODEL"
    listed = client.get("/training-jobs").json()["items"][0]
    assert listed["steps"]["TRAINING"] == "FINISHED" and listed["currentStep"] == "TRAINED_MODEL"


def test_progress_never_goes_back(client, mlmr):
    """Reporting a step behind the current one is refused with 409 `TRAINING_JOB_ILLEGAL_TRANSITION`, so progress cannot be rewound."""
    job_id = _start(client, mlmr)
    _progress(client, job_id, "TRAINING")
    resp = _progress(client, job_id, "DATA_EXTRACTION")
    assert resp.status_code == 409 and resp.json()["detail"]["title"] == "TRAINING_JOB_ILLEGAL_TRANSITION"


def test_a_suspended_or_ended_run_makes_no_progress(client, mlmr):
    """Only an IN_PROGRESS run takes progress: a SUSPENDED run shows its step as SUSPENDED and refuses reports until resumed, and a cancelled run refuses them for good."""
    job_id = _start(client, mlmr)
    client.post(f"/training-jobs/{job_id}/suspend")
    assert _steps(client, job_id)[1]["DATA_EXTRACTION"] == "SUSPENDED"
    assert _progress(client, job_id, "TRAINING").status_code == 409
    client.post(f"/training-jobs/{job_id}/resume")
    assert _progress(client, job_id, "TRAINING").status_code == 200
    client.delete(f"/training-jobs/{job_id}")
    assert _progress(client, job_id, "TRAINED_MODEL").status_code == 409


def test_how_the_run_ended_shows_on_the_step_it_reached(client, mlmr):
    """A failed or cancelled run shows FAILED or CANCELLED on the step it had reached, earlier steps FINISHED and later ones NOT_STARTED."""
    failed = _start(client, mlmr)
    _progress(client, failed, "TRAINING")
    client.post(f"/training-jobs/{failed}/complete", json={"succeeded": False})
    assert _steps(client, failed)[1] == {"DATA_EXTRACTION": "FINISHED", "TRAINING": "FAILED", "TRAINED_MODEL": "NOT_STARTED"}

    cancelled = _start(client, mlmr)
    client.delete(f"/training-jobs/{cancelled}")
    assert _steps(client, cancelled)[1]["DATA_EXTRACTION"] == "CANCELLED"


def test_a_finished_run_finished_every_step(client, mlmr):
    """A FINISHED run shows every step FINISHED, whichever step the runtime last reported: completion is the report that the trained model exists."""
    job_id = _start(client, mlmr)
    client.post(f"/training-jobs/{job_id}/complete", json={"succeeded": True})
    assert set(_steps(client, job_id)[1].values()) == {"FINISHED"}


def test_progress_validates_the_step_and_the_job(client, mlmr):
    """An unknown step name is a 422 and an unknown job id a 404."""
    job_id = _start(client, mlmr)
    assert _progress(client, job_id, "DEPLOYING").status_code == 422
    assert _progress(client, str(uuid.uuid4()), "TRAINING").status_code == 404


# ---------------------------------------------------------------- OI-5-aiml-featuregroup-dme

class FakeDme:
    """A recording double of the DME routes AIMgF uses for a feature group's data job: `post` stores the job definition and answers 202 with a `dataJobId` (or the `refuse` status and title, to
    play a DME refusal), `delete` forgets the job.
    """
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
    """Replaces the R1 client's `post` and `delete` with a `FakeDme` for the test and returns it; the test inspects `dme.jobs` and sets `dme.refuse` to make DME refuse the job."""
    fake = FakeDme()
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: fake.post(path, json=json, **kw))
    monkeypatch.setattr("app.main.R1Client.delete", lambda self, path, **kw: fake.delete(path, **kw))
    return fake


def test_an_enable_dme_group_creates_its_dme_data_job(client, dme):
    """An `enableDme` group creates one CONTINUOUS TRAINING-stage DME data job for its type, consumer `aimgf:feature-group:<name>`, whose definition carries the features and filters, and stores the job id on the group."""
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
    """A group registered without `enableDme` makes no DME call and has no data job id."""
    assert client.post("/feature-groups", json=_feature_group_body()).json()["dmeDataJobId"] is None
    assert dme.jobs == {}


def test_a_refused_dme_job_means_no_group(client, dme):
    """When DME refuses the data job the answer is 422 `FEATURE_GROUP_DME_JOB_REFUSED` carrying DME's reason and no group is stored."""
    dme.refuse = (409, "DELIVERY_METHOD_NOT_OFFERED")
    resp = client.post("/feature-groups", json=_feature_group_body(enableDme=True, dmeTypeId=str(uuid.uuid4()),
                                                                   dataDeliveryMethod="STREAMING_KAFKA"))
    assert resp.status_code == 422 and resp.json()["detail"]["title"] == "FEATURE_GROUP_DME_JOB_REFUSED"
    assert "DELIVERY_METHOD_NOT_OFFERED" in resp.json()["detail"]["detail"]
    assert client.get("/feature-groups").json()["total"] == 0


def test_a_duplicate_name_is_refused_before_any_job_is_created(client, dme):
    """A duplicate group name is refused with 409 before the DME job is created, so a rejected request leaves no orphan data job."""
    client.post("/feature-groups", json=_feature_group_body())
    resp = client.post("/feature-groups", json=_feature_group_body(enableDme=True, dmeTypeId=str(uuid.uuid4())))
    assert resp.status_code == 409 and dme.jobs == {}


def test_deleting_a_group_terminates_its_dme_job(client, dme):
    """Deleting a group terminates its DME data job and reports `dmeDataJobTeardown` DONE; the group is gone and a second delete is 404."""
    client.post("/feature-groups", json=_feature_group_body(enableDme=True, dmeTypeId=str(uuid.uuid4())))
    assert client.get("/feature-groups/cellCounters").json()["dmeDataJobId"] in dme.jobs

    resp = client.delete("/feature-groups/cellCounters")

    assert resp.json() == {"featureGroupName": "cellCounters", "dmeDataJobTeardown": "DONE"}
    assert dme.jobs == {}
    assert client.get("/feature-groups/cellCounters").status_code == 404
    assert client.delete("/feature-groups/cellCounters").json()["detail"]["title"] == "FEATURE_GROUP_NOT_FOUND"


def test_deleting_a_group_without_a_job_skips_the_teardown(client, dme):
    """Deleting a group that has no data job reports the teardown as SKIPPED."""
    client.post("/feature-groups", json=_feature_group_body())
    assert client.delete("/feature-groups/cellCounters").json()["dmeDataJobTeardown"] == "SKIPPED"
