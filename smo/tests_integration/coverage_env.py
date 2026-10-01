"""Scenario helpers for the Coverage Optimization rApp integration tests and
demo (Wave 10.3, W10.3-10): one gNB with a four-cell cluster behind the mock
O1 adaptor, its coverage PM feed, the sample package, and the operator's
governance steps — all through real routes.

The live PM is produced from the cells' **current O1 settings**, read back
from the mock adaptor, through the sample's propagation model. A tilt or
power change the rApp makes is therefore visible in the next hour's PM."""

import datetime
from pathlib import Path

from energy_saving_env import OPERATOR, governance, heartbeat, ok  # noqa: F401 (OPERATOR re-exported for the tests)

SMO_ROOT = Path(__file__).resolve().parent.parent
CSAR = SMO_ROOT / "samples" / "coverage-optimization-rapp.csar"
CSAR_URL = "http://example/coverage-optimization-rapp.csar"
RAPP = "coverage-optimization-rapp"
ME = "gnb-cco-01"
HISTORY_START = datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC)
LIVE_START = HISTORY_START + datetime.timedelta(days=3)


def serve_csar(loaded_apps, monkeypatch):
    import httpx
    real_get = httpx.get
    csar = CSAR.read_bytes()

    class Resp:
        content = csar

        def raise_for_status(self):
            pass

    current = loaded_apps["onboarding"].httpx.get if hasattr(loaded_apps["onboarding"].httpx, "get") else real_get
    monkeypatch.setattr(loaded_apps["onboarding"].httpx, "get",
                        lambda location, timeout=None, **kw: Resp() if location == CSAR_URL else current(location, timeout=timeout, **kw))


def producer(loaded_apps):
    return loaded_apps[RAPP].producer


def build_ran(mesh, me=ME, reset=True):
    if reset:
        ok(mesh["mock-o1-adaptor"].delete("/state"))
        endpoint = ok(mesh["ran-nf-oam"].post("/o1-adaptor-endpoints", json={
            "managedElementRef": me, "adaptorUri": "http://mock-o1-adaptor:8000/edit-config", "protocolSupport": ["NETCONF"],
            "o1Protocol": "NETCONF", "entityType": "O-DU", "vendorName": "mock-vendor"}))
        ok(mesh["ran-nf-oam"].post(f"/o1-adaptor-endpoints/{endpoint['endpointId']}/heartbeat"))
        ok(mesh["sa-smos"].post("/o1-cm-handler/registration", json={}))
    ok(mesh["ran-nf-oam"].post("/pm-subscriptions", params={"managed_element_ref": me, "counter_type": "COVERAGE_PERFORMANCE",
                                                           "delivery_method": "pull", "granularity_period": 3600}))
    ok(mesh[RAPP].post("/sim-producer/register"))


def onboard(mesh):
    package = ok(mesh["onboarding"].post("/packages", json={"location": CSAR_URL}))
    return package["packageId"], ok(mesh["onboarding"].get(f"/packages/{package['packageId']}/onboarding-status"))


def topology(loaded_apps, cells=None) -> dict[str, list[str]]:
    """The sample cluster's neighbour lists, its cells renamed to `cells`
    (in order) when given — e.g. onto the EnergySaving rApp's 101..104."""
    gen = producer(loaded_apps)
    rename = dict(zip(gen.CELLS, cells or gen.CELLS))
    return {rename[c]: [rename[j] for j in nbrs] for c, nbrs in gen.NEIGHBOURS.items()}


def create_instance(mesh, package_id, mode, me=ME, cells=None, **config):
    cells = cells or ["301", "302", "303", "304"]
    created = ok(mesh["rapp-mgmt"].post("/instances", json={
        "packageId": package_id, "autonomyMode": mode, "config": {"managedElementRef": me, "cells": cells, **config},
        "regionScope": {"objectInstance": me, "cells": cells}}))
    return created["instanceId"]


def report(mesh, measurements, me=ME):
    return ok(mesh["ran-nf-oam"].post("/pm-reports", json={"managedElementRef": me, "counterType": "COVERAGE_PERFORMANCE",
                                                           "measurements": measurements}), 201)


def setting(mesh, cell, me=ME) -> tuple[int, int]:
    """The cell's live (digitalTilt, configuredMaxTxPower) on the mock adaptor."""
    get = lambda mfr: ok(mesh["mock-o1-adaptor"].get(f"/objects/{me}", params={"function_ref": mfr}))["attributes"]  # noqa: E731
    return int(get(f"CommonBeamformingFunction={cell}")["digitalTilt"]), int(get(f"NRSectorCarrier={cell}")["configuredMaxTxPower"])


def lifecycle(mesh, loaded_apps, instance_id):
    rapp = mesh[RAPP]
    out = {"train": ok(rapp.post(f"/instances/{instance_id}/lifecycle/train"))}
    model_id = out["train"]["modelId"]
    governance(mesh, model_id, "APPROVE_TRAINING")
    out["validate"] = ok(rapp.post(f"/instances/{instance_id}/lifecycle/validate"))
    governance(mesh, model_id, "APPROVE_VALIDATION")
    ok(rapp.post("/sim-producer/publish", json={
        "managedElementRef": "digital-twin-cco", "start": HISTORY_START.isoformat(), "hours": 12,
        "clusters": {"dt1": {"scenario": "OVERSHOOT", "faultCell": "a"}, "dt2": {"scenario": "WEAK_COVERAGE", "faultCell": "b"},
                     "dt3": {"scenario": "PILOT_POLLUTION", "faultCell": "c"}, "dt4": {"scenario": "HEALTHY"}}}))
    out["emulate"] = ok(rapp.post(f"/instances/{instance_id}/lifecycle/emulate"))
    governance(mesh, model_id, "SUBMIT_FOR_APPROVAL", "APPROVE", "CERTIFY", "PROMOTE")
    out["deploy"] = ok(rapp.post(f"/instances/{instance_id}/lifecycle/deploy"))
    out["modelId"] = model_id
    return out


def scenario(mesh, loaded_apps, monkeypatch, instances: dict[str, str], me=ME, reset=True, guards=None, cells=None, **config):
    """Deployed instances ({name: autonomyMode}) sharing one cluster; 72 h of
    history in which tilt and power were stepped, reported after every
    instance started."""
    serve_csar(loaded_apps, monkeypatch)
    build_ran(mesh, me, reset)
    for cell, g in (guards or {}).items():
        ok(mesh["ran-nf-oam"].put(f"/managed-entities/{me}/cells/{cell}/guards", json=g))
    package_id, _ = onboard(mesh)
    ids = {name: create_instance(mesh, package_id, mode, me, cells, **config) for name, mode in instances.items()}
    for iid in ids.values():
        ok(mesh[RAPP].post(f"/instances/{iid}/start"))
    report(mesh, producer(loaded_apps).history(topology(loaded_apps, cells), HISTORY_START, 72), me)
    return {name: {"instanceId": iid, **lifecycle(mesh, loaded_apps, iid)} for name, iid in ids.items()}


class Clock:
    """Hourly live PM from LIVE_START, produced from the cells' live O1
    settings and the injected `faults` ({cell: scenario})."""

    def __init__(self, mesh, loaded_apps, me=ME, start=LIVE_START, cells=None):
        self.mesh, self.apps, self.me, self.now = mesh, loaded_apps, me, start
        self.neighbours = topology(loaded_apps, cells)

    def settings(self):
        return {c: setting(self.mesh, c, self.me) for c in self.neighbours}

    def hour(self, faults=None, hours=1, overrides=None):
        heartbeat(self.mesh, self.me)
        gen = producer(self.apps)
        for _ in range(hours):
            report(self.mesh, gen.measurements(self.neighbours, self.settings(), faults or {}, self.now, overrides), self.me)
            self.now += datetime.timedelta(hours=1)
        return self


def evaluate(mesh, instance_id, correlation_id=None):
    headers = {"X-Correlation-ID": correlation_id} if correlation_id else {}
    return ok(mesh[RAPP].post(f"/instances/{instance_id}/evaluate", headers=headers))


def decision(result, cell):
    return next(d for d in result["decisions"] if d["cellId"] == cell)


def cell_state(mesh, instance_id, cell):
    return next(c for c in ok(mesh[RAPP].get(f"/instances/{instance_id}/cells"))["items"] if c["cellId"] == cell)


def fault(mesh, function_ref, mode, count=1, me=ME):
    ok(mesh["mock-o1-adaptor"].post("/faults", json={"mode": mode, "count": count, "managedObjectRef": f"{me}/{function_ref}"}))
