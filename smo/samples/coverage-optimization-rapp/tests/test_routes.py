"""The Coverage Optimization rApp's HTTP routes (instance binding, lifecycle, the evaluate loop with its KPI-verified revert of a
change set, coordination with the other rApps, audit views, the Digital Twin producer) through FastAPI's TestClient on SQLite,
with the AI Runtime SDK and R1 replaced by an in-memory platform double (`FakePlatform`): a configuration store the double's O1
actions change and its read-back reports, scripted autonomy dispatches, and the real model and decision engine on top.
Run with: pytest samples/coverage-optimization-rapp/tests -q"""

import datetime
import json
import types
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from smo_sdk import SdkError
from smo_sdk.data import AlarmScope
from smo_shared.db import Base, get_session
from smo_shared.testing import make_test_engine

from app import main
from app.main import app
from app.producer import BASELINE_POWER, BASELINE_TILT, CELLS, NEIGHBOURS, history, measurements, sim_cluster

ME = "gnb"
T0 = datetime.datetime(2026, 9, 4, 12, 0, tzinfo=datetime.UTC)
OVERSHOOT = {"301": "OVERSHOOT"}
EVERYTHING = {"301": "OVERSHOOT", "302": "WEAK_COVERAGE", "303": "PILOT_POLLUTION"}


def _window(hour, faults):
    return [{"managedElementRef": ME, **m} for m in measurements(NEIGHBOURS, {}, faults, T0 + datetime.timedelta(hours=hour))]


def _sim(scenario, fault_cell="a", hours=4):
    topology = sim_cluster("dt1")
    return [{"managedElementRef": ME, **m} for h in range(hours) for m in measurements(
        topology, {}, {} if scenario == "HEALTHY" else {f"dt1-{fault_cell}": scenario}, T0 + datetime.timedelta(hours=h),
        extra={"cluster": "dt1", "scenario": scenario, "faultCell": f"dt1-{fault_cell}"})]


def _raise(error):
    def call(*a, **k):
        raise error
    return call


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code, self._payload = status_code, payload

    def json(self):
        return self._payload


class FakeR1:
    """rapp-mgmt's instance records and the other rApps' published state, by path."""
    def __init__(self):
        self.paths = {}

    def get(self, path, **kw):
        status, payload = self.paths.get(path, (404, {}))
        return FakeResponse(status, payload)


class FakePlatform:
    def __init__(self):
        self.config = {}                   # managed function ref -> attributes, what O1 read-back reports
        for c in CELLS:
            self.config[f"CommonBeamformingFunction={c}"] = {"digitalTilt": BASELINE_TILT}
            self.config[f"NRSectorCarrier={c}"] = {"configuredMaxTxPower": BASELINE_POWER}
        self.records = {"TRAINING": history(NEIGHBOURS, T0, 72), "INFERENCE": _window(0, OVERSHOOT), "EMULATION": _sim("OVERSHOOT")}
        self.guards, self.alarms = [], []
        self.alarms_fail = False
        self.dispatch = {"status": "SHADOWED"}
        self.action_status = "COMPLETED"   # what the intent handler reports for an enacted change
        self.stuck = False                 # O1 acknowledges a write but the configuration does not change
        self.actions, self.dispatches, self.training, self.expectations = [], [], [], []
        self.artifact = b""
        self.data = types.SimpleNamespace(
            get_dataset=self.get_dataset, read_config=self.read_config, query_cell_guards=lambda managed_element_ref: self.guards,
            query_critical_alarms=self.query_alarms, register_type=lambda *a, **k: {"dmeTypeId": "t-1"},
            discover_types=lambda ns: [{"dmeTypeId": "t-sim", "dmeTypeIdStruct": {"name": "COVERAGE_PERFORMANCE_SIM"}}],
            list_data_jobs=lambda dme_type_id: [{"dataJobId": "j-1"}], ingest_data_record=lambda job, record: None)
        self.models = types.SimpleNamespace(
            register_model=lambda *a, **k: {"modelId": str(uuid.uuid4())}, store_model=self.store_model,
            download_artifact=lambda model_id, version: types.SimpleNamespace(content=self.artifact))
        self.lifecycle = types.SimpleNamespace(
            start_training=lambda *a, **k: {"trainingJobId": "tr-1"}, complete_training=self.complete_training,
            start_validation=lambda *a, **k: {"validationJobId": "va-1"},
            complete_validation=lambda job, ok, metrics: {"status": "VALIDATED" if ok else "FAILED"},
            start_emulation=lambda *a, **k: {"emulationJobId": "em-1"},
            complete_emulation=lambda job, ok, metrics: {"status": "EMULATED" if ok else "FAILED"},
            deploy_model=lambda *a, **k: {}, deploy_runtime=lambda *a, **k: {}, activate_runtime=lambda *a, **k: {},
            get_model_lifecycle=lambda model_id: {"state": "X"}, request_inference=lambda model_id: {"inferenceJobId": "inf-1"},
            resolve_inference=lambda job, ok, inference_outputs: {"aIMLInferenceReportId": "rep-1"})
        self.intent = types.SimpleNamespace(
            request_autonomy_dispatch=self.request_dispatch, get_autonomy_dispatch=self.get_dispatch,
            list_intent_reports=self.list_intent_reports)
        self.platform = types.SimpleNamespace(execute_action=self.execute_action)

    def get_dataset(self, name, consumer, lifecycle_stage=None, max_records=2000):
        return {"dmeTypeId": "t", "dataJobId": f"job-{lifecycle_stage}", "sourceDomain": "RAN", "records": self.records[lifecycle_stage]}

    def read_config(self, me, mfr):
        return {"attributes": self.config.get(mfr, {})}

    def query_alarms(self, me):
        if self.alarms_fail:
            raise SdkError(503, {"detail": "down"})
        return AlarmScope(self.alarms)

    def store_model(self, model_type, version, artifact, filename):
        self.artifact = artifact
        return {"artifactVersion": 5}

    def complete_training(self, job, ok, metrics, **kw):
        self.training.append((ok, metrics))
        return {"status": "TRAINED" if ok else "FAILED"}

    def execute_action(self, consumer, changes, action_id, source_context):
        self.actions.append({"changes": changes, "actionId": action_id, "context": source_context})
        if not self.stuck:
            for change in changes:
                self.config.setdefault(change["managedFunctionRef"], {}).update(change["attributeChanges"])
        return {"actionId": str(action_id), "status": "COMPLETED", "forwardedJobId": "fj-1"}

    def request_dispatch(self, instance_id, expectations, rmih_id, **kw):
        self.dispatches.append((instance_id, expectations, kw))
        self.expectations = expectations
        return self.get_dispatch(None)

    def get_dispatch(self, dispatch_id):
        if self.dispatch["status"] == "DISPATCHED" and not self.stuck:
            for e in self.expectations:
                cell = e["expectationObject"]["objectContexts"][0]["contextValueRange"][0]
                target = e["expectationTargets"][0]
                function, attribute = target["targetName"].split(".")
                self.config.setdefault(f"{function}={cell}", {})[attribute] = target["targetValueRange"]
        return self.dispatch

    def list_intent_reports(self, intent_id):
        if self.dispatch.get("noReport"):
            return [{"attributes": {}}, {"attributes": {"intentFulfilmentReport": {"additionalFulfilmentInfo": "{not json"}}}]
        info = json.dumps({"actions": [{"expectationId": e["expectationId"], "actionId": "a-1", "status": self.action_status}
                                       for e in self.expectations]})
        return [{"attributes": {}}, {"attributes": {"intentFulfilmentReport": {"additionalFulfilmentInfo": info}}}]


@pytest.fixture
def platform(monkeypatch):
    fake = FakePlatform()
    monkeypatch.setattr(main, "sdk", fake)
    return fake


@pytest.fixture
def r1(monkeypatch):
    fake = FakeR1()
    monkeypatch.setattr(main, "_r1", fake)
    return fake


@pytest.fixture
def client():
    engine = make_test_engine()
    Base.metadata.create_all(engine, tables=[m.__table__ for m in (main.CoverageInstance, main.CoverageCell, main.CoverageDecision)])
    factory = sessionmaker(bind=engine)

    def override_get_session():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _start(client, r1, mode="AUTONOMOUS", **config):
    instance_id = str(uuid.uuid4())
    r1.paths[f"/rapp-mgmt/instances/{instance_id}"] = (200, {
        "packageId": str(uuid.uuid4()), "autonomyMode": mode, "configuration": {"managedElementRef": ME, "cells": CELLS, **config}})
    resp = client.post(f"/instances/{instance_id}/start")
    assert resp.status_code == 200, resp.text
    return instance_id


def _deployed(client, platform, r1, mode="AUTONOMOUS", **config):
    instance_id = _start(client, r1, mode, **config)
    for step in ("train", "validate", "emulate", "deploy"):
        resp = client.post(f"/instances/{instance_id}/lifecycle/{step}")
        assert resp.status_code == 200, resp.text
    return instance_id


def _dispatched(mode="AUTONOMOUS", **extra):
    return {"status": "DISPATCHED", "dispatchId": str(uuid.uuid4()), "autonomyMode": mode, "intentId": "i-1", **extra}


def _evaluate(client, instance_id, **headers):
    resp = client.post(f"/instances/{instance_id}/evaluate", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _moved(body):
    return [d for d in body["decisions"] if d["decision"] not in ("NO_CHANGE", "REVERT")]


# ---------------------------------------------------------------- instance binding

def test_start_binds_the_instance_and_discovers_a_dataset_per_stage(client, platform, r1):
    instance_id = _start(client, r1, "ASSIST", baselineTilt=50, baselinePower=40, mobilityInstanceId="mo-1", energySavingInstanceId="es-1")
    view = client.get(f"/instances/{instance_id}").json()
    assert (view["cells"], view["baselineTilt"], view["baselinePower"], view["autonomyMode"]) == (CELLS, 50, 40, "ASSIST")
    assert (view["mobilityInstanceId"], view["energySavingInstanceId"], view["rmihId"]) == ("mo-1", "es-1", "sa-smos")
    assert set(view["datasets"]) == {"TRAINING", "INFERENCE", "EMULATION"}
    assert [i["instanceId"] for i in client.get("/instances").json()["items"]] == [instance_id]
    assert [c["cellId"] for c in client.get(f"/instances/{instance_id}/cells").json()["items"]] == CELLS
    assert client.post(f"/instances/{instance_id}/start").status_code == 200       # a second start re-binds
    assert len(client.get("/instances").json()["items"]) == 1


def test_start_rejects_an_unknown_instance_and_an_invalid_config(client, platform, r1):
    resp = client.post(f"/instances/{uuid.uuid4()}/start")
    assert (resp.status_code, resp.json()["detail"]["title"]) == (404, "INSTANCE_NOT_FOUND")
    for config in ({"cells": []}, {"managedElementRef": ""}):
        instance_id = str(uuid.uuid4())
        r1.paths[f"/rapp-mgmt/instances/{instance_id}"] = (200, {"configuration": {"managedElementRef": ME, "cells": CELLS, **config}})
        resp = client.post(f"/instances/{instance_id}/start")
        assert (resp.status_code, resp.json()["detail"]["title"]) == (422, "INSTANCE_CONFIG_INVALID")


def test_an_instance_that_was_never_started_is_404_on_every_route(client, platform, r1):
    missing = uuid.uuid4()
    for verb, path in (("get", ""), ("post", "/lifecycle/deploy"), ("post", "/evaluate"), ("post", "/reconcile"),
                       ("get", "/cells"), ("get", "/dashboard")):
        resp = getattr(client, verb)(f"/instances/{missing}{path}")
        assert (resp.status_code, resp.json()["detail"]["title"]) == (404, "INSTANCE_NOT_STARTED"), path


def test_a_platform_error_is_passed_through_and_a_server_error_becomes_502(client, platform, r1, monkeypatch):
    instance_id = _start(client, r1)
    monkeypatch.setattr(platform.lifecycle, "start_training", _raise(SdkError(409, {"detail": {"title": "BUSY", "detail": "x"}})))
    resp = client.post(f"/instances/{instance_id}/lifecycle/train")
    assert (resp.status_code, resp.json()["detail"]["title"]) == (409, "BUSY")
    monkeypatch.setattr(platform.lifecycle, "start_training", _raise(SdkError(500, {"detail": "boom"})))
    resp = client.post(f"/instances/{instance_id}/lifecycle/train")
    assert (resp.status_code, resp.json()["detail"]["title"]) == (502, "PLATFORM_ERROR")


# ---------------------------------------------------------------- lifecycle

def test_the_model_lifecycle_trains_validates_emulates_and_deploys(client, platform, r1):
    instance_id = _start(client, r1)
    trained = client.post(f"/instances/{instance_id}/lifecycle/train").json()
    assert (trained["status"], trained["metrics"]["artifactVersion"]) == ("TRAINED", 5)
    validated = client.post(f"/instances/{instance_id}/lifecycle/validate").json()
    assert (validated["validationJobId"], validated["status"]) == ("va-1", "VALIDATED" if validated["passed"] else "FAILED")
    emulated = client.post(f"/instances/{instance_id}/lifecycle/emulate").json()
    assert (emulated["emulationJobId"], emulated["status"]) == ("em-1", "EMULATED" if emulated["passed"] else "FAILED")
    deployed = client.post(f"/instances/{instance_id}/lifecycle/deploy").json()
    assert deployed["artifactVersion"] == 5 and deployed["model"]
    view = client.get(f"/instances/{instance_id}").json()
    assert view["lifecycleJobs"] == {"training": "tr-1", "validation": "va-1", "emulation": "em-1"}
    assert client.post(f"/instances/{instance_id}/lifecycle/train").json()["modelId"] == view["modelId"]


def test_training_on_too_little_history_is_422_and_the_job_is_failed(client, platform, r1):
    instance_id = _start(client, r1)
    platform.records["TRAINING"] = platform.records["TRAINING"][:2]
    resp = client.post(f"/instances/{instance_id}/lifecycle/train")
    assert (resp.status_code, resp.json()["detail"]["title"]) == (422, "TRAINING_FAILED")
    assert platform.training[0][0] is False and platform.training[0][1]["failureReason"]
    assert client.get(f"/instances/{instance_id}").json()["lifecycleJobs"] == {"training": "tr-1"}


# ---------------------------------------------------------------- evaluate

def test_evaluate_needs_a_deployed_model(client, platform, r1):
    instance_id = _start(client, r1)
    resp = client.post(f"/instances/{instance_id}/evaluate")
    assert (resp.status_code, resp.json()["detail"]["title"]) == (409, "MODEL_NOT_DEPLOYED")


def test_evaluate_without_pm_data_decides_nothing(client, platform, r1):
    instance_id = _deployed(client, platform, r1)
    platform.records["INFERENCE"] = []
    assert _evaluate(client, instance_id)["decisions"] == []


def test_evaluate_in_shadow_mode_plans_a_move_and_writes_nothing(client, platform, r1):
    instance_id = _deployed(client, platform, r1, "SHADOW")
    platform.dispatch = {"status": "SHADOWED", "dispatchId": str(uuid.uuid4()), "autonomyMode": "SHADOW"}
    body = _evaluate(client, instance_id, **{"X-Correlation-ID": "exec-1"})
    moves = _moved(body)
    assert body["executionId"] == "exec-1" and body["plan"]["moves"] and moves
    assert all(d["outcome"] == "SHADOWED" and d["intent"]["status"] == "SHADOWED" for d in moves)
    assert platform.actions == [] and len(platform.dispatches[0][1]) == len(moves)
    assert client.get(f"/instances/{instance_id}").json()["observing"] is None


def test_an_enacted_change_set_is_verified_and_then_confirmed_by_the_kpi(client, platform, r1):
    instance_id = _deployed(client, platform, r1)
    platform.dispatch = _dispatched()
    body = _evaluate(client, instance_id)
    moves = _moved(body)
    assert moves and all(d["outcome"] == "EXECUTED" and d["verification"]["result"] == "VERIFIED" for d in moves)
    observing = client.get(f"/instances/{instance_id}").json()["observing"]
    assert set(observing["cells"]) == {d["cellId"] for d in moves}
    states = {c["cellId"]: c["state"] for c in client.get(f"/instances/{instance_id}/cells").json()["items"]}
    assert {c for c, s in states.items() if s == "OBSERVING"} == set(observing["cells"])
    # until an hour of post-change PM exists nothing else moves
    waiting = _evaluate(client, instance_id)
    assert {d["reason"] for d in waiting["decisions"]} == {"OBSERVING"} and waiting["kpi"] is None
    # the cluster is healthy an hour later: the set is confirmed
    platform.records["INFERENCE"] += _window(1, {})
    confirmed = _evaluate(client, instance_id)
    reasons = {d["cellId"]: d["reason"] for d in confirmed["decisions"]}
    assert confirmed["kpi"]["verdict"] == "IMPROVED_OR_EQUAL"
    assert {reasons[c] for c in observing["cells"]} == {"CHANGE_CONFIRMED"}
    assert client.get(f"/instances/{instance_id}").json()["observing"] is None


def test_a_change_set_that_degrades_the_cluster_is_reverted_through_dme(client, platform, r1):
    instance_id = _deployed(client, platform, r1)
    platform.dispatch = _dispatched()
    moves = _moved(_evaluate(client, instance_id))
    changed = moves[0]
    platform.records["INFERENCE"] += _window(1, EVERYTHING)
    body = _evaluate(client, instance_id)
    reverted = {d["cellId"]: d for d in body["decisions"]}[changed["cellId"]]
    assert body["kpi"]["verdict"] == "DEGRADED"
    assert (reverted["decision"], reverted["reason"], reverted["outcome"]) == ("REVERT", "KPI_DEGRADED", "REVERTED")
    assert platform.actions[-1]["context"]["reason"] == "REVERT:KPI_DEGRADED"
    assert reverted["finalState"]["digitalTilt"] == BASELINE_TILT and reverted["finalState"]["configuredMaxTxPower"] == BASELINE_POWER
    assert client.get(f"/instances/{instance_id}").json()["observing"] is None


def test_a_revert_that_will_not_stick_is_retried_once_and_reported(client, platform, r1):
    instance_id = _deployed(client, platform, r1)
    platform.dispatch = _dispatched()
    moves = _moved(_evaluate(client, instance_id))
    changed = moves[0]
    platform.records["INFERENCE"] += _window(1, EVERYTHING)
    platform.stuck = True
    body = _evaluate(client, instance_id)
    reverted = {d["cellId"]: d for d in body["decisions"]}[changed["cellId"]]
    assert reverted["outcome"] == "REVERT_FAILED" and reverted["verification"]["result"] == "VERIFY_FAILED"
    assert len(platform.actions) == 2 * len(moves)      # each reverted cell was written, then re-sent once


def test_a_cell_outside_the_change_set_is_only_reviewed(client, platform, r1):
    instance_id = _deployed(client, platform, r1)
    platform.dispatch = _dispatched()
    changed = {d["cellId"] for d in _moved(_evaluate(client, instance_id))}
    platform.records["INFERENCE"] += _window(1, {})
    reasons = {d["cellId"]: d["reason"] for d in _evaluate(client, instance_id)["decisions"]}
    assert {reasons[c] for c in set(CELLS) - changed} == {"CHANGE_SET_REVIEW"}


def test_a_write_that_o1_reports_but_does_not_apply_is_rolled_back(client, platform, r1):
    instance_id = _deployed(client, platform, r1)
    platform.dispatch = _dispatched()
    platform.stuck = True
    moves = _moved(_evaluate(client, instance_id))
    assert moves and all(d["outcome"] == "VERIFY_FAILED_ROLLED_BACK" and d["rollback"]["result"] == "ALREADY_RESTORED" for d in moves)
    assert all(d["verification"]["result"] == "VERIFY_FAILED" for d in moves)
    assert client.get(f"/instances/{instance_id}").json()["observing"] is None


def test_a_rollback_that_cannot_be_verified_is_reported(client, platform, r1):
    instance_id = _deployed(client, platform, r1)
    platform.dispatch = _dispatched()
    platform.stuck = True
    for cell in CELLS:           # the live settings are unreadable, so the restore is written twice and never confirmed
        platform.config[f"CommonBeamformingFunction={cell}"] = {}
        platform.config[f"NRSectorCarrier={cell}"] = {}
    moves = _moved(_evaluate(client, instance_id))
    assert moves and all(d["outcome"] == "VERIFY_FAILED_ROLLBACK_FAILED" and len(d["rollback"]["attempts"]) == 2 for d in moves)


@pytest.mark.parametrize("status,trigger", [("PARTIAL_SUCCESS", "PARTIAL_SUCCESS"), ("FAILED", "ACTION_FAILED")])
def test_a_change_the_intent_handler_did_not_complete_is_rolled_back(client, platform, r1, status, trigger):
    instance_id = _deployed(client, platform, r1)
    platform.dispatch = _dispatched()
    platform.action_status = status
    moves = _moved(_evaluate(client, instance_id))
    assert moves and all(d["outcome"] == f"{trigger}_ROLLED_BACK" and d["verification"] is None for d in moves)


def test_a_change_the_intent_never_reported_is_rolled_back_as_failed(client, platform, r1):
    instance_id = _deployed(client, platform, r1)
    platform.dispatch = _dispatched(noReport=True)
    moves = _moved(_evaluate(client, instance_id))
    assert moves and all(d["outcome"] == "ACTION_FAILED_ROLLED_BACK" and d["action"]["status"] == "NOT_ENACTED" for d in moves)


def test_assist_mode_waits_for_the_operator_then_enacts_on_reconcile(client, platform, r1):
    instance_id = _deployed(client, platform, r1, "ASSIST")
    dispatch_id = str(uuid.uuid4())
    platform.dispatch = {"status": "AWAITING_SCOPE", "dispatchId": dispatch_id, "autonomyMode": "ASSIST"}
    moves = _moved(_evaluate(client, instance_id))
    assert moves and {d["outcome"] for d in moves} == {"AWAITING_APPROVAL"}
    assert client.get(f"/instances/{instance_id}").json()["pendingDispatchId"] == dispatch_id
    # while it waits every cell is recorded as waiting, and a still-pending dispatch settles nothing
    assert {d["reason"] for d in _evaluate(client, instance_id)["decisions"]} == {"AWAITING_APPROVAL"}
    assert client.post(f"/instances/{instance_id}/reconcile").json() == {"settled": []}
    platform.dispatch = {**_dispatched("ASSIST"), "dispatchId": dispatch_id}
    settled = client.post(f"/instances/{instance_id}/reconcile").json()["settled"]
    assert settled[0]["status"] == "DISPATCHED" and set(settled[0]["outcomes"].values()) == {"EXECUTED"}
    view = client.get(f"/instances/{instance_id}").json()
    assert view["pendingDispatchId"] is None and view["observing"]["cells"]
    assert client.post(f"/instances/{instance_id}/reconcile").json() == {"settled": []}


def test_assist_mode_rejection_leaves_the_cluster_unchanged(client, platform, r1):
    instance_id = _deployed(client, platform, r1, "ASSIST")
    dispatch_id = str(uuid.uuid4())
    platform.dispatch = {"status": "AWAITING_SCOPE", "dispatchId": dispatch_id, "autonomyMode": "ASSIST"}
    _evaluate(client, instance_id)
    platform.dispatch = {"status": "REJECTED", "dispatchId": dispatch_id, "autonomyMode": "ASSIST", "rejectedBy": "op"}
    settled = client.post(f"/instances/{instance_id}/reconcile").json()["settled"]
    assert set(settled[0]["outcomes"].values()) == {"REJECTED"}
    view = client.get(f"/instances/{instance_id}").json()
    assert view["pendingDispatchId"] is None and view["observing"] is None
    assert platform.actions == []


def test_the_guards_block_every_cell_and_the_audit_trail_says_why(client, platform, r1):
    instance_id = _deployed(client, platform, r1, energySavingInstanceId="es-1", mobilityInstanceId="mo-1")
    platform.guards = [{"cellId": "301", "cellClass": "EMERGENCY"}]
    platform.alarms = [{"alarmId": "al-1", "severity": "critical", "probableCause": "x", "managedFunctionRef": "NRCellDU=302"}]
    platform.config["NRCellDU=303"] = {"administrativeState": "LOCKED"}
    r1.paths["/rapps/es-1/operator/instances/es-1/cells"] = (200, {"items": [{"cellId": "304", "state": "SLEEP",
                                                                          "lastUnlockedAt": "2026-09-04T11:50:00Z"}]})
    r1.paths["/rapps/mo-1/operator/instances/mo-1/relations"] = (200, {"items": [
        {"relation": "301-302", "source": "301", "target": "302", "state": "OBSERVING"},
        {"relation": "303-304", "source": "303", "target": "304", "state": "STEADY"}]})
    body = _evaluate(client, instance_id)
    assert _moved(body) == [] and platform.dispatches == []
    by_cell = {d["cellId"]: d for d in body["decisions"]}
    assert by_cell["301"]["reason"].startswith("SAFETY_BLOCKED:PROTECTED_CELL")
    assert "CELL_ASLEEP" in by_cell["303"]["reason"] and by_cell["304"]["safety"]["esState"] == "SLEEP"
    assert "MRO_OBSERVING" in by_cell["302"]["reason"]


def test_unreachable_peers_and_an_unreadable_alarm_list_hold_nothing(client, platform, r1):
    instance_id = _deployed(client, platform, r1, "SHADOW", energySavingInstanceId="es-1", mobilityInstanceId="mo-1")
    platform.alarms_fail = True
    platform.dispatch = {"status": "SHADOWED", "dispatchId": str(uuid.uuid4()), "autonomyMode": "SHADOW"}
    body = _evaluate(client, instance_id)
    assert _moved(body) and all(d["safety"]["criticalAlarmIds"] == [] for d in body["decisions"])


# ---------------------------------------------------------------- audit and dashboard

def test_decisions_are_listed_filtered_and_shown_on_the_dashboard(client, platform, r1):
    instance_id = _deployed(client, platform, r1, "SHADOW")
    platform.dispatch = {"status": "SHADOWED", "dispatchId": str(uuid.uuid4()), "autonomyMode": "SHADOW"}
    for execution in ("e-1", "e-2"):
        _evaluate(client, instance_id, **{"X-Correlation-ID": execution})
    assert len(client.get(f"/instances/{instance_id}/decisions").json()["items"]) == 2 * len(CELLS)
    only = client.get(f"/instances/{instance_id}/decisions", params={"execution_id": "e-1", "cell_id": "301", "limit": 5}).json()["items"]
    assert [(d["executionId"], d["cellId"]) for d in only] == [("e-1", "301")]
    assert client.get(f"/instances/{instance_id}/decisions", params={"cell_id": "other"}).json()["items"] == []
    board = client.get(f"/instances/{instance_id}/dashboard", params={"points": 1}).json()
    cell = board["cells"][0]
    assert board["instance"]["instanceId"] == instance_id and len(board["cells"]) == len(CELLS)
    assert len(cell["shareTrend"]) == 1 and len(cell["excessTrend"]) == 1 and cell["latestDecision"]["executionId"] in ("e-1", "e-2")


def test_the_dashboard_of_a_cluster_without_decisions_has_no_latest_decision(client, platform, r1):
    instance_id = _start(client, r1)
    assert {c["latestDecision"] for c in client.get(f"/instances/{instance_id}/dashboard").json()["cells"]} == {None}


# ---------------------------------------------------------------- the Digital Twin producer

def test_the_sim_producer_registers_publishes_and_answers_dme_callbacks(client, platform):
    resp = client.post("/sim-producer/register")
    assert resp.status_code == 201 and resp.json()["dmeTypeId"] == "t-1"
    body = {"managedElementRef": ME, "clusters": {"dt1": {"scenario": "OVERSHOOT", "faultCell": "b"}, "dt2": {}}, "start": T0.isoformat(), "hours": 2}
    assert client.post("/sim-producer/publish", json=body).json() == {"dmeTypeId": "t-sim", "dataJobs": 1, "recordsDelivered": 16}
    assert client.post("/sim-producer/publish", json={"clusters": {}}).status_code == 422
    assert client.get("/sim-producer/health").json() == {"status": "healthy"}
    assert client.post("/sim-producer/jobs", json={"dataJobId": "j"}).json() == {"status": "accepted"}
    assert client.delete("/sim-producer/jobs/j-1").status_code == 204
