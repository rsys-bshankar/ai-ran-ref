"""GUI-9.8 and GUI-9.4 in AIMgF: a training run's epoch progress and ETA (`epoch`/`totalEpochs` on `POST /training-jobs/{id}/progress` and in the
metrics writeback, `etaSeconds` on every job answer) and the count of models per lifecycle state (`GET /model-lifecycles/counts`).

Fixtures `client`, `mlmr` and `db_session_factory` come from `test_main.py` (SQLite, the MLMR and NFO doubles); a run is started through
`POST /training-jobs` and its start time moved back in the database where a test needs elapsed time. Run:
`cd smo/aimgf && PYTHONPATH=.:../shared python -m pytest tests/test_training_progress_and_counts.py -q`.
"""

import datetime
import uuid

from test_main import client, db_session_factory, mlmr  # noqa: F401  (pytest fixtures)

from app.models import ModelLifecycle, TrainingJob


def _start(client, mlmr):
    """Starts a training run for a fresh model and returns the job id."""
    model_id = mlmr.add_model()
    return client.post("/training-jobs", json={"modelId": str(model_id), "producerId": "rapp-1"}).json()["trainingJobId"]


def _started_seconds_ago(db_session_factory, job_id, seconds):
    """Moves the run's `started_at` `seconds` into the past, so the ETA has an elapsed time to work with."""
    with db_session_factory() as db:
        job = db.get(TrainingJob, uuid.UUID(job_id))
        job.started_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=seconds)
        db.commit()


def test_a_new_run_has_no_epochs_and_no_eta(client, mlmr):
    """Before the runtime reports anything the progress fields are null, so the GUI shows no made-up estimate."""
    job_id = _start(client, mlmr)
    view = client.get(f"/training-jobs/{job_id}/status").json()
    assert (view["epoch"], view["totalEpochs"], view["progressUpdatedAt"], view["etaSeconds"]) == (None, None, None, None)


def test_the_eta_is_the_pace_so_far_times_the_epochs_left(client, mlmr, db_session_factory):
    """Two of ten epochs in 100 s leaves eight at 50 s each: an ETA of about 400 s, on the status, list and progress answers alike."""
    job_id = _start(client, mlmr)
    _started_seconds_ago(db_session_factory, job_id, 100)
    resp = client.post(f"/training-jobs/{job_id}/progress", json={"step": "TRAINING", "epoch": 2, "totalEpochs": 10})
    assert resp.status_code == 200 and resp.json()["currentStep"] == "TRAINING" and 395 <= resp.json()["etaSeconds"] <= 405
    status = client.get(f"/training-jobs/{job_id}/status").json()
    assert status["epoch"] == 2 and status["totalEpochs"] == 10 and status["progressUpdatedAt"] and 395 <= status["etaSeconds"] <= 405
    assert 395 <= client.get("/training-jobs").json()["items"][0]["etaSeconds"] <= 405


def test_epochs_can_be_reported_without_a_step_and_one_at_a_time(client, mlmr):
    """An epoch-only report leaves the step alone, and a report of only the epoch keeps the total given earlier."""
    job_id = _start(client, mlmr)
    assert client.post(f"/training-jobs/{job_id}/progress", json={"totalEpochs": 5}).status_code == 200
    body = client.post(f"/training-jobs/{job_id}/progress", json={"epoch": 3}).json()
    assert body["currentStep"] == "DATA_EXTRACTION" and (body["epoch"], body["totalEpochs"]) == (3, 5)


def test_no_eta_until_an_epoch_is_finished_or_once_the_run_is_not_running(client, mlmr):
    """Epoch 0 gives no pace to extrapolate from, and a suspended or finished run has no ETA although its epochs stay visible."""
    job_id = _start(client, mlmr)
    assert client.post(f"/training-jobs/{job_id}/progress", json={"epoch": 0, "totalEpochs": 4}).json()["etaSeconds"] is None
    client.post(f"/training-jobs/{job_id}/progress", json={"epoch": 1})
    client.post(f"/training-jobs/{job_id}/suspend")
    view = client.get(f"/training-jobs/{job_id}/status").json()
    assert view["etaSeconds"] is None and view["epoch"] == 1


def test_a_bad_epoch_report_is_refused(client, mlmr):
    """An empty report, an epoch beyond the total or a negative epoch is a 422; a run that is not IN_PROGRESS still refuses with 409."""
    job_id = _start(client, mlmr)
    assert client.post(f"/training-jobs/{job_id}/progress", json={}).status_code == 422
    assert client.post(f"/training-jobs/{job_id}/progress", json={"epoch": 6, "totalEpochs": 5}).status_code == 422
    assert client.post(f"/training-jobs/{job_id}/progress", json={"epoch": -1}).status_code == 422
    client.post(f"/training-jobs/{job_id}/suspend")
    assert client.post(f"/training-jobs/{job_id}/progress", json={"epoch": 1}).status_code == 409


def test_the_metrics_writeback_records_epochs_it_carries(client, mlmr, db_session_factory):
    """A runtime that only writes metrics per epoch still feeds the ETA: whole-number `epoch`/`totalEpochs` keys are recorded as progress."""
    job_id = _start(client, mlmr)
    _started_seconds_ago(db_session_factory, job_id, 60)
    resp = client.post(f"/training-jobs/{job_id}/model-metrics", json={"loss": 0.2, "epoch": 3, "totalEpochs": 6})
    assert resp.status_code == 200 and resp.json()["modelMetrics"]["loss"] == 0.2
    view = client.get(f"/training-jobs/{job_id}/status").json()
    assert (view["epoch"], view["totalEpochs"]) == (3, 6) and 55 <= view["etaSeconds"] <= 65


def test_metrics_without_whole_number_epochs_change_no_progress(client, mlmr):
    """Metrics without the keys, or with values that are not whole numbers, are stored as metrics only."""
    job_id = _start(client, mlmr)
    client.post(f"/training-jobs/{job_id}/model-metrics", json={"loss": 0.2, "epoch": "three", "totalEpochs": 2.5})
    view = client.get(f"/training-jobs/{job_id}/status").json()
    assert view["epoch"] is None and view["totalEpochs"] is None and view["progressUpdatedAt"] is None


def test_models_are_counted_by_lifecycle_state(client, db_session_factory):
    """One row per state with its count, largest first: the models-by-stage tiles come from one GROUP BY, not a read of every lifecycle."""
    with db_session_factory() as db:
        for state in ("TRAINING", "PROMOTED", "PROMOTED", "PROMOTED", "TRAINING", "RETIRED"):
            db.add(ModelLifecycle(model_id=uuid.uuid4(), model_lifecycle_state=state))
        db.commit()
    assert client.get("/model-lifecycles/counts").json() == {"groups": [
        {"state": "PROMOTED", "count": 3}, {"state": "TRAINING", "count": 2}, {"state": "RETIRED", "count": 1}]}


def test_no_lifecycles_count_as_no_groups(client):
    """An AIMgF that has acted on no model answers an empty list, not an error."""
    assert client.get("/model-lifecycles/counts").json() == {"groups": []}
