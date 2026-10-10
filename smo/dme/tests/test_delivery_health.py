"""GUI-9.8 in DME: a data job's delivery health. `lastDeliveryAt` is set when a producer delivers a record (`POST /data-jobs/{id}/records`), and
`late` says whether two of the job's declared `expectedIntervalSeconds` passed without one; `GET /data-jobs?late=` filters on it in SQL.

Uses the `client` fixture of `test_main.py` (SQLite, producer callbacks made harmless) and `register_type_body`; time is moved by patching
`app.main._now` with `_clock`. Run: `cd smo/dme && PYTHONPATH=.:../shared python -m pytest tests/test_delivery_health.py -q`.
"""

import datetime

import pytest

from test_main import _default_producer_callbacks_are_harmless, client, register_type_body  # noqa: F401  (pytest fixtures)

T0 = datetime.datetime(2026, 10, 10, 8, 0, tzinfo=datetime.UTC)


@pytest.fixture
def clock(monkeypatch):
    """A settable clock in place of `app.main._now`: `clock["now"]` is the time the app sees, starting at T0."""
    state = {"now": T0}
    monkeypatch.setattr("app.main._now", lambda: state["now"])
    return state


def _job(client, interval=None, consumer="rapp-1"):
    """Registers a type and creates a CONTINUOUS pull job for it, declaring `interval` seconds when given; returns the job id."""
    reg = client.post("/production-capabilities", json=register_type_body(name=f"T{consumer}")).json()
    body = {"dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"], "dataDeliveryMethod": "PULL_HTTP", "consumerId": consumer}
    if interval is not None:
        body["expectedIntervalSeconds"] = interval
    return client.post("/data-jobs", json=body).json()["dataJobId"]


def _view(client, job_id):
    """The job as `GET /data-jobs/{id}` answers it."""
    return client.get(f"/data-jobs/{job_id}").json()


def test_a_job_without_an_interval_is_never_late_or_on_time(client, clock):
    """No declared interval means no verdict: `late` is null, while the last delivery is still recorded."""
    job_id = _job(client)
    assert (_view(client, job_id)["late"], _view(client, job_id)["lastDeliveryAt"]) == (None, None)
    client.post(f"/data-jobs/{job_id}/records", json={"payload": {"kpi": 1}})
    view = _view(client, job_id)
    assert view["late"] is None and view["lastDeliveryAt"] == T0.isoformat()


def test_a_job_turns_late_two_intervals_after_its_last_delivery(client, clock):
    """With a 60 s interval: on time 119 s after a delivery, LATE at 121 s, on time again after the next delivery."""
    job_id = _job(client, interval=60)
    client.post(f"/data-jobs/{job_id}/records", json={"payload": {}})
    clock["now"] = T0 + datetime.timedelta(seconds=119)
    assert _view(client, job_id)["late"] is False
    clock["now"] = T0 + datetime.timedelta(seconds=121)
    assert _view(client, job_id)["late"] is True
    client.post(f"/data-jobs/{job_id}/records", json={"payload": {}})
    view = _view(client, job_id)
    assert view["late"] is False and view["lastDeliveryAt"] == clock["now"].isoformat() and view["expectedIntervalSeconds"] == 60


def test_a_job_that_never_delivered_is_late_two_intervals_after_it_was_declared(client, clock):
    """Before the first delivery the clock starts when the job is declared, so a producer that never delivers is noticed."""
    job_id = _job(client, interval=30)
    clock["now"] = T0 + datetime.timedelta(seconds=61)
    view = _view(client, job_id)
    assert view["late"] is True and view["lastDeliveryAt"] is None


def test_an_update_can_declare_or_drop_the_interval(client, clock):
    """PUT replaces the declaration: adding an interval starts the clock, leaving it out removes the verdict."""
    job_id = _job(client)
    job = _view(client, job_id)
    body = {k: job[k] for k in ("dataDeliveryMode", "dmeTypeId", "dataDeliveryMethod", "consumerId")}
    client.put(f"/data-jobs/{job_id}", json={**body, "expectedIntervalSeconds": 10})
    clock["now"] = T0 + datetime.timedelta(seconds=21)
    assert _view(client, job_id)["late"] is True
    client.put(f"/data-jobs/{job_id}", json=body)
    assert _view(client, job_id)["late"] is None


def test_the_list_filters_late_and_on_time_jobs(client, clock):
    """`late=true` and `late=false` split the jobs that declare an interval; a job declaring none is in neither."""
    stale, fresh, undeclared = _job(client, 60, "a"), _job(client, 600, "b"), _job(client, None, "c")
    clock["now"] = T0 + datetime.timedelta(seconds=300)

    def ids(late):
        # the ids of the jobs the list answers for this filter
        return {j["dataJobId"] for j in client.get("/data-jobs", params={"late": late}).json()["items"]}

    assert ids(True) == {stale} and ids(False) == {fresh}
    assert undeclared not in ids(True) | ids(False)


def test_a_non_positive_interval_is_refused(client):
    """An interval of zero or less cannot be met or missed: 422."""
    reg = client.post("/production-capabilities", json=register_type_body()).json()
    resp = client.post("/data-jobs", json={"dataDeliveryMode": "CONTINUOUS", "dmeTypeId": reg["registrationId"],
                                           "dataDeliveryMethod": "PULL_HTTP", "consumerId": "rapp-1", "expectedIntervalSeconds": 0})
    assert resp.status_code == 422
