"""Scenario helpers for the Mobility Optimization rApp integration tests and
demo (Wave 10.2, W10.2-10): one gNB-DU with four cells and four neighbour
relations behind the mock O1 adaptor, its handover PM feed, the sample
package, and the operator's governance steps — all through real routes."""

import datetime
from pathlib import Path

from energy_saving_env import OPERATOR, governance, heartbeat, ok

SMO_ROOT = Path(__file__).resolve().parent.parent
CSAR = SMO_ROOT / "samples" / "mobility-optimization-rapp.csar"
CSAR_URL = "http://example/mobility-optimization-rapp.csar"
RAPP = "mobility-optimization-rapp"
ME = "gnb-du-mro-01"
RELATIONS = [{"relation": "201-202", "source": "201", "target": "202"},
             {"relation": "201-203", "source": "201", "target": "203"},
             {"relation": "202-203", "source": "202", "target": "203"},
             {"relation": "203-204", "source": "203", "target": "204"}]
HISTORY = {"201-202": "HEALTHY", "201-203": "TOO_LATE", "202-203": "TOO_EARLY", "203-204": "PING_PONG"}
HISTORY_START = datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC)
LIVE_START = HISTORY_START + datetime.timedelta(days=3)


def serve_csar(loaded_apps, monkeypatch):
    """Makes Onboarding's download of the sample's package URL return the committed .csar bytes (every other URL goes to the real `httpx.get`), for
    the duration of the test.
    """
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
    """Builds the RAN the scenario needs behind the mock O1 adaptor: with `reset`, clears the adaptor and registers and activates it, and registers
    the SA SMOS O1 handler; always subscribes the PM counter type and registers the rApp's Digital Twin producer.
    """
    if reset:
        ok(mesh["mock-o1-adaptor"].delete("/state"))
        endpoint = ok(mesh["ran-nf-oam"].post("/o1-adaptor-endpoints", json={
            "managedElementRef": me, "adaptorUri": "http://mock-o1-adaptor:8000/edit-config", "protocolSupport": ["NETCONF"],
            "o1Protocol": "NETCONF", "entityType": "O-DU", "vendorName": "mock-vendor"}))
        ok(mesh["ran-nf-oam"].post(f"/o1-adaptor-endpoints/{endpoint['endpointId']}/heartbeat"))
        ok(mesh["sa-smos"].post("/o1-cm-handler/registration", json={}))
    ok(mesh["ran-nf-oam"].post("/pm-subscriptions", params={"managed_element_ref": me, "counter_type": "HO_PERFORMANCE",
                                                           "delivery_method": "pull", "granularity_period": 3600}))
    ok(mesh[RAPP].post("/sim-producer/register"))


def onboard(mesh):
    """Uploads the sample's package to Onboarding by URL and returns (packageId, the onboarding status)."""
    package = ok(mesh["onboarding"].post("/packages", json={"location": CSAR_URL}))
    return package["packageId"], ok(mesh["onboarding"].get(f"/packages/{package['packageId']}/onboarding-status"))


def create_instance(mesh, package_id, mode, me=ME, relations=RELATIONS, **config):
    """Creates an rApp Management instance of the sample in `mode` for the managed element and its neighbour relations, with the rApp's operator
    API base and a region scope covering the relations; returns the instance id. `config` adds to the instance configuration.
    """
    created = ok(mesh["rapp-mgmt"].post("/instances", json={
        "operatorApiBase": "http://mobility-optimization-rapp:8000",     # where the gateway's /rapps/{instanceId}/operator/... reaches this rApp (GUI-8.3)
        "packageId": package_id, "autonomyMode": mode,
        "config": {"managedElementRef": me, "relations": relations, **config},
        "regionScope": {"objectInstance": me, "cells": [r["relation"] for r in relations]}}))
    return created["instanceId"]


def report(mesh, loaded_apps, scenarios: dict[str, str], start: datetime.datetime, hours: int, me=ME, overrides=None):
    """Hourly handover PM per relation; `overrides` {relation: counters} replaces the generated counters."""
    gen = producer(loaded_apps)
    measurements = []
    for rel, scenario in scenarios.items():
        for h in range(hours):
            t = start + datetime.timedelta(hours=h)
            values = (overrides or {}).get(rel) or gen.relation_counters(rel, t, scenario)
            measurements.append({"cellId": rel.split("-")[0], "relation": rel, "values": values, "timestamp": t.isoformat()})
    return ok(mesh["ran-nf-oam"].post("/pm-reports", json={"managedElementRef": me, "counterType": "HO_PERFORMANCE",
                                                           "measurements": measurements}), 201)


def lifecycle(mesh, instance_id):
    """Runs the model lifecycle through the rApp's routes with the operator's governance steps between them (train, approve, validate, approve,
    publish the Digital Twin data, emulate, certify and promote, deploy) and returns each step's answer and the model id.
    """
    rapp = mesh[RAPP]
    out = {"train": ok(rapp.post(f"/instances/{instance_id}/lifecycle/train"))}
    model_id = out["train"]["modelId"]
    governance(mesh, model_id, "APPROVE_TRAINING")
    out["validate"] = ok(rapp.post(f"/instances/{instance_id}/lifecycle/validate"))
    governance(mesh, model_id, "APPROVE_VALIDATION")
    ok(rapp.post("/sim-producer/publish", json={
        "managedElementRef": "digital-twin-mro", "start": HISTORY_START.isoformat(), "hours": 48,
        "relations": {"a-b": "TOO_LATE", "b-c": "TOO_EARLY", "c-d": "HEALTHY", "d-e": "WRONG_CELL", "e-f": "PING_PONG"}}))
    out["emulate"] = ok(rapp.post(f"/instances/{instance_id}/lifecycle/emulate"))
    governance(mesh, model_id, "SUBMIT_FOR_APPROVAL", "APPROVE", "CERTIFY", "PROMOTE")
    out["deploy"] = ok(rapp.post(f"/instances/{instance_id}/lifecycle/deploy"))
    out["modelId"] = model_id
    return out


def scenario(mesh, loaded_apps, monkeypatch, instances: dict[str, str], me=ME, relations=RELATIONS, history=HISTORY,
             reset=True, guards=None, **config):
    """Deployed instances ({name: autonomyMode}) sharing one RAN, history reported after every instance started."""
    serve_csar(loaded_apps, monkeypatch)
    build_ran(mesh, me, reset)
    for cell, g in (guards or {}).items():
        ok(mesh["ran-nf-oam"].put(f"/managed-entities/{me}/cells/{cell}/guards", json=g))
    package_id, _ = onboard(mesh)
    ids = {name: create_instance(mesh, package_id, mode, me, relations, **config) for name, mode in instances.items()}
    for iid in ids.values():
        ok(mesh[RAPP].post(f"/instances/{iid}/start"))
    report(mesh, loaded_apps, history, HISTORY_START, 72, me)
    out = {name: {"instanceId": iid, **lifecycle(mesh, iid)} for name, iid in ids.items()}
    return out


class Clock:
    """Hourly live PM from LIVE_START; each call reports one or more windows."""

    def __init__(self, mesh, loaded_apps, me=ME, start=LIVE_START):
        self.mesh, self.apps, self.me, self.now = mesh, loaded_apps, me, start

    def hour(self, hours=1, overrides=None, **scenarios):
        """scenarios: r201_202="TOO_LATE" … (relation ids with '_' for '-')."""
        heartbeat(self.mesh, self.me)
        report(self.mesh, self.apps, {k.removeprefix("r").replace("_", "-"): v for k, v in scenarios.items()},
               self.now, hours, self.me, overrides)
        self.now += datetime.timedelta(hours=hours)
        return self


def evaluate(mesh, instance_id, correlation_id=None):
    """Runs one closed-loop pass over the instance through the rApp's `evaluate` route, with `correlation_id` as the X-Correlation-ID (the
    execution id in the audit trail) when given, and returns the answer.
    """
    headers = {"X-Correlation-ID": correlation_id} if correlation_id else {}
    return ok(mesh[RAPP].post(f"/instances/{instance_id}/evaluate", headers=headers))


def decision(result, relation):
    return next(d for d in result["decisions"] if d["relation"] == relation)


def cio(mesh, relation, me=ME):
    """The cell individual offset the mock O1 adaptor currently holds for a relation."""
    attrs = ok(mesh["mock-o1-adaptor"].get(f"/objects/{me}", params={"function_ref": f"NRCellRelation={relation}"}))["attributes"]
    return attrs["cellIndividualOffset"]


def relation_state(mesh, instance_id, relation):
    return next(r for r in ok(mesh[RAPP].get(f"/instances/{instance_id}/relations"))["items"] if r["relation"] == relation)


def fault(mesh, relation, mode, count=1, me=ME):
    ok(mesh["mock-o1-adaptor"].post("/faults", json={"mode": mode, "count": count,
                                                     "managedObjectRef": f"{me}/NRCellRelation={relation}"}))


__all__ = ["OPERATOR"]
