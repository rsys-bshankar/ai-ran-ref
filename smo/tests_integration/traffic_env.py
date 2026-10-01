"""Scenario helpers for the Traffic Steering rApp integration tests and demo
(Wave 10.4, W10.4-10): one gNB with a four-cell, two-layer cluster behind the
mock O1 adaptor, its load PM feed, the sample package, and the operator's
governance steps — all through real routes.

The live PM is produced from the cells' **current O1 steering settings**
(each relation's CIO and each cell's reselection priority towards the other
layer, read back from the mock adaptor) through the sample's load model. A
step the rApp takes is therefore visible in the next hour's PM."""

import datetime
import json
from pathlib import Path

from energy_saving_env import OPERATOR, governance, ok  # noqa: F401 (OPERATOR re-exported for the tests)

SMO_ROOT = Path(__file__).resolve().parent.parent
CSAR = SMO_ROOT / "samples" / "traffic-steering-rapp.csar"
CSAR_URL = "http://example/traffic-steering-rapp.csar"
RAPP = "traffic-steering-rapp"
ME = "gnb-mlb-01"
HISTORY_START = datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC)
LIVE_START = HISTORY_START + datetime.timedelta(days=3, hours=12)   # the busy afternoon


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


def topology(loaded_apps, cells=None) -> tuple[dict[str, list[str]], dict[str, str]]:
    """The sample cluster's neighbours and layers, its cells renamed to
    `cells` (in order) when given."""
    gen = producer(loaded_apps)
    rename = dict(zip(gen.CELLS, cells or gen.CELLS))
    return ({rename[c]: [rename[j] for j in nbrs] for c, nbrs in gen.NEIGHBOURS.items()},
            {rename[c]: layer for c, layer in gen.LAYERS.items()})


def build_ran(mesh, me=ME, reset=True):
    if reset:
        ok(mesh["mock-o1-adaptor"].delete("/state"))
        endpoint = ok(mesh["ran-nf-oam"].post("/o1-adaptor-endpoints", json={
            "managedElementRef": me, "adaptorUri": "http://mock-o1-adaptor:8000/edit-config", "protocolSupport": ["NETCONF"],
            "o1Protocol": "NETCONF", "entityType": "O-DU", "vendorName": "mock-vendor"}))
        ok(mesh["ran-nf-oam"].post(f"/o1-adaptor-endpoints/{endpoint['endpointId']}/heartbeat"))
        ok(mesh["sa-smos"].post("/o1-cm-handler/registration", json={}))
    ok(mesh["ran-nf-oam"].post("/pm-subscriptions", params={"managed_element_ref": me, "counter_type": "LOAD_PERFORMANCE",
                                                           "delivery_method": "pull", "granularity_period": 3600}))
    ok(mesh[RAPP].post("/sim-producer/register"))


def onboard(mesh):
    package = ok(mesh["onboarding"].post("/packages", json={"location": CSAR_URL}))
    return package["packageId"], ok(mesh["onboarding"].get(f"/packages/{package['packageId']}/onboarding-status"))


def region(neighbours, layers) -> list[str]:
    """What the instance may write: every relation (<source>-<target>) and
    every cell's relation to another layer (<cell>-<layer>)."""
    return sorted({f"{c}-{t}" for c, nbrs in neighbours.items() for t in nbrs} |
                  {f"{c}-{layers[t]}" for c, nbrs in neighbours.items() for t in nbrs if layers[t] != layers[c]})


def create_instance(mesh, package_id, mode, neighbours, layers, me=ME, **config):
    cells = [{"cellId": c, "layer": layer} for c, layer in layers.items()]
    created = ok(mesh["rapp-mgmt"].post("/instances", json={
        "packageId": package_id, "autonomyMode": mode, "config": {"managedElementRef": me, "cells": cells, **config},
        "regionScope": {"objectInstance": me, "cells": region(neighbours, layers)}}))
    return created["instanceId"]


def report(mesh, measurements, me=ME):
    return ok(mesh["ran-nf-oam"].post("/pm-reports", json={"managedElementRef": me, "counterType": "LOAD_PERFORMANCE",
                                                           "measurements": measurements}), 201)


def attrs(mesh, mfr, me=ME) -> dict:
    return ok(mesh["mock-o1-adaptor"].get(f"/objects/{me}", params={"function_ref": mfr}))["attributes"]


def cio(mesh, source, target, me=ME) -> int:
    return json.loads(attrs(mesh, f"NRCellRelation={source}-{target}", me)["cellIndividualOffset"])[0]


def priority(mesh, cell, layer, me=ME) -> int:
    return int(attrs(mesh, f"NRFreqRelation={cell}-{layer}", me)["cellReselectionPriority"])


def lifecycle(mesh, instance_id):
    rapp = mesh[RAPP]
    out = {"train": ok(rapp.post(f"/instances/{instance_id}/lifecycle/train"))}
    model_id = out["train"]["modelId"]
    governance(mesh, model_id, "APPROVE_TRAINING")
    out["validate"] = ok(rapp.post(f"/instances/{instance_id}/lifecycle/validate"))
    governance(mesh, model_id, "APPROVE_VALIDATION")
    ok(rapp.post("/sim-producer/publish", json={
        "managedElementRef": "digital-twin-mlb", "start": HISTORY_START.isoformat(), "hours": 24,
        "clusters": {"dt1": {"scenario": "HOTSPOT", "hotCell": "a"}, "dt2": {"scenario": "HOTSPOT", "hotCell": "c"},
                     "dt3": {"scenario": "HEALTHY"}}}))
    out["emulate"] = ok(rapp.post(f"/instances/{instance_id}/lifecycle/emulate"))
    governance(mesh, model_id, "SUBMIT_FOR_APPROVAL", "APPROVE", "CERTIFY", "PROMOTE")
    out["deploy"] = ok(rapp.post(f"/instances/{instance_id}/lifecycle/deploy"))
    out["modelId"] = model_id
    return out


def scenario(mesh, loaded_apps, monkeypatch, instances: dict[str, str], me=ME, reset=True, guards=None, cells=None, **config):
    """Deployed instances ({name: autonomyMode}) sharing one cluster; 72 h of
    history in which the biases were stepped, reported after every instance
    started."""
    serve_csar(loaded_apps, monkeypatch)
    build_ran(mesh, me, reset)
    for cell, g in (guards or {}).items():
        ok(mesh["ran-nf-oam"].put(f"/managed-entities/{me}/cells/{cell}/guards", json=g))
    package_id, _ = onboard(mesh)
    nbrs, layers = topology(loaded_apps, cells)
    ids = {name: create_instance(mesh, package_id, mode, nbrs, layers, me, **config) for name, mode in instances.items()}
    for iid in ids.values():
        ok(mesh[RAPP].post(f"/instances/{iid}/start"))
    report(mesh, producer(loaded_apps).history(nbrs, layers, HISTORY_START, 72), me)
    return {name: {"instanceId": iid, **lifecycle(mesh, iid)} for name, iid in ids.items()}


class Clock:
    """Hourly live PM from LIVE_START, produced from the cells' live O1
    steering settings and the injected `faults` ({cell: "HOTSPOT"})."""

    def __init__(self, mesh, loaded_apps, me=ME, start=LIVE_START, cells=None):
        self.mesh, self.apps, self.me, self.now = mesh, loaded_apps, me, start
        self.neighbours, self.layers = topology(loaded_apps, cells)

    def settings(self):
        return {c: {"cio": {t: cio(self.mesh, c, t, self.me) for t in nbrs},
                    "prio": {self.layers[t]: priority(self.mesh, c, self.layers[t], self.me)
                             for t in nbrs if self.layers[t] != self.layers[c]}}
                for c, nbrs in self.neighbours.items()}

    def hour(self, faults=None, hours=1, overrides=None):
        gen = producer(self.apps)
        for _ in range(hours):
            report(self.mesh, gen.measurements(self.neighbours, self.layers, self.settings(), faults or {}, self.now,
                                               overrides), self.me)
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
