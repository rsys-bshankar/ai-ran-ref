"""Scenario helpers for the EnergySaving rApp integration tests and demo
(Wave 10.1, W10-26/W10-27): the RAN (one gNB-DU with four cells behind the
mock O1 adaptor), the PM feed, the sample package, and the operator's
governance steps. Everything goes through the services' real routes.
"""

import datetime
import uuid
from pathlib import Path

SMO_ROOT = Path(__file__).resolve().parent.parent
CSAR = SMO_ROOT / "samples" / "energy-saving-rapp.csar"
CSAR_URL = "http://example/energy-saving-rapp.csar"
ME = "gnb-du-es-01"
CELLS = ["101", "102", "103", "104"]
OPERATOR = "noc-operator"
HISTORY_START = datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC)
HISTORY_DAYS = 3
LIVE_START = HISTORY_START + datetime.timedelta(days=HISTORY_DAYS)  # midnight after the history


def ok(resp, *codes):
    assert resp.status_code in (codes or (200, 201, 202, 204)), f"{resp.status_code}: {resp.text}"
    return resp.json() if resp.content else None


def serve_csar(loaded_apps, monkeypatch):
    """Onboarding fetches the package over HTTP; serve the built CSAR."""
    import httpx
    real_get = httpx.get
    csar = CSAR.read_bytes()

    class Resp:
        content = csar

        def raise_for_status(self):
            pass

    monkeypatch.setattr(loaded_apps["onboarding"].httpx, "get",
                        lambda location, timeout=None, **kw: Resp() if location == CSAR_URL else real_get(location, timeout=timeout, **kw))


def build_ran(mesh):
    """The RAN and platform plumbing the rApp relies on."""
    ok(mesh["mock-o1-adaptor"].delete("/state"))
    endpoint = ok(mesh["ran-nf-oam"].post("/o1-adaptor-endpoints", json={
        "managedElementRef": ME, "adaptorUri": "http://mock-o1-adaptor:8000/edit-config", "protocolSupport": ["NETCONF"],
        "o1Protocol": "NETCONF", "entityType": "O-DU", "vendorName": "mock-vendor"}))
    ok(mesh["ran-nf-oam"].post(f"/o1-adaptor-endpoints/{endpoint['endpointId']}/heartbeat"))
    # O1 PM: RAN NF OAM's PM subscription registers the PRB_UTILIZATION DME type
    ok(mesh["ran-nf-oam"].post("/pm-subscriptions", params={"managed_element_ref": ME, "counter_type": "PRB_UTILIZATION",
                                                           "delivery_method": "pull", "granularity_period": 300}))
    ok(mesh["energy-saving-rapp"].post("/sim-producer/register"))       # the Digital Twin dataset
    ok(mesh["sa-smos"].post("/o1-cm-handler/registration", json={}))   # the generic O1-CM intent handler (W8-07)


def guard(mesh, cell, **guards):
    return ok(mesh["ran-nf-oam"].put(f"/managed-entities/{ME}/cells/{cell}/guards", json=guards))


def onboard(mesh):
    package = ok(mesh["onboarding"].post("/packages", json={"location": CSAR_URL}))
    return package["packageId"], ok(mesh["onboarding"].get(f"/packages/{package['packageId']}/onboarding-status"))


def create_instance(mesh, package_id, mode, actuator="ADMINISTRATIVE_STATE", cells=CELLS):
    created = ok(mesh["rapp-mgmt"].post("/instances", json={
        "packageId": package_id, "autonomyMode": mode,
        "config": {"managedElementRef": ME, "cells": cells, "actuator": actuator},
        "regionScope": {"objectInstance": ME, "cells": cells}}))
    return created["instanceId"]


def pm(mesh, values: dict[str, list[float]], start: datetime.datetime, step_minutes: int = 5):
    """An NF PM report: per cell, one sample every `step_minutes` from `start`."""
    measurements = [{"cellId": cell, "value": v, "timestamp": (start + datetime.timedelta(minutes=i * step_minutes)).isoformat()}
                    for cell, series in values.items() for i, v in enumerate(series)]
    return ok(mesh["ran-nf-oam"].post("/pm-reports", json={"managedElementRef": ME, "counterType": "PRB_UTILIZATION",
                                                           "measurements": measurements}), 201)


def history(mesh, cells=CELLS):
    """HISTORY_DAYS of hourly diurnal PRB for training/validation."""
    values = {cell: [_diurnal(cell, HISTORY_START + datetime.timedelta(hours=h)) for h in range(24 * HISTORY_DAYS)]
              for cell in cells}
    return pm(mesh, values, HISTORY_START, step_minutes=60)


def _diurnal(cell, t):
    """The rApp package's own synthetic daily profile (samples/energy-saving-rapp/app/producer.py)."""
    import sys
    rapp = sys.modules.get("energy_saving_producer")
    if rapp is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("energy_saving_producer",
                                                      SMO_ROOT / "samples" / "energy-saving-rapp" / "app" / "producer.py")
        rapp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rapp)
        sys.modules["energy_saving_producer"] = rapp
    return rapp.diurnal_prb(cell, t)


def governance(mesh, model_id, *events):
    for event in events:
        ok(mesh["aimgf"].post(f"/models/{model_id}/advance", params={"event": event, "decided_by": OPERATOR,
                                                                     "rationale": f"{event} by {OPERATOR}"}))


def lifecycle(mesh, instance_id):
    """Train → validate → emulate, with the operator's approvals between,
    certification and promotion, then the MLIF deployment."""
    rapp = mesh["energy-saving-rapp"]
    out = {"train": ok(rapp.post(f"/instances/{instance_id}/lifecycle/train"))}
    model_id = out["train"]["modelId"]
    governance(mesh, model_id, "APPROVE_TRAINING")
    out["validate"] = ok(rapp.post(f"/instances/{instance_id}/lifecycle/validate"))
    governance(mesh, model_id, "APPROVE_VALIDATION")
    ok(mesh["energy-saving-rapp"].post("/sim-producer/publish", json={
        "managedElementRef": "digital-twin-01", "cells": ["dt-1", "dt-2"], "start": HISTORY_START.isoformat(), "hours": 48}))
    out["emulate"] = ok(rapp.post(f"/instances/{instance_id}/lifecycle/emulate"))
    governance(mesh, model_id, "SUBMIT_FOR_APPROVAL", "APPROVE", "CERTIFY")
    out["certified"] = ok(mesh["aimgf"].get(f"/models/{model_id}/lifecycle"))
    governance(mesh, model_id, "PROMOTE")
    out["deploy"] = ok(rapp.post(f"/instances/{instance_id}/lifecycle/deploy"))
    out["modelId"] = model_id
    return out


def ready(mesh, loaded_apps, monkeypatch, mode="AUTONOMOUS", actuator="ADMINISTRATIVE_STATE", guards=None):
    """One deployed instance in `mode`, history loaded."""
    return scenario(mesh, loaded_apps, monkeypatch, {"rapp": (mode, actuator)}, guards)["rapp"]


def scenario(mesh, loaded_apps, monkeypatch, instances: dict[str, tuple[str, str]], guards=None):
    """Deployed instances ({name: (autonomyMode, actuator)}) of one onboarded
    package, sharing the RAN. Every instance is started (its data jobs
    opened) before the history is reported, so each receives it."""
    serve_csar(loaded_apps, monkeypatch)
    build_ran(mesh)
    for cell, g in (guards or {}).items():
        guard(mesh, cell, **g)
    package_id, _ = onboard(mesh)
    ids = {name: create_instance(mesh, package_id, mode, actuator) for name, (mode, actuator) in instances.items()}
    for iid in ids.values():
        ok(mesh["energy-saving-rapp"].post(f"/instances/{iid}/start"))
    history(mesh)
    for iid in ids.values():
        lifecycle(mesh, iid)
    return ids


class Clock:
    """Live PM reporting: every call reports `minutes` of 5-minute samples
    per cell from where the last call stopped (starting at LIVE_START)."""

    def __init__(self, mesh):
        self.mesh, self.now = mesh, LIVE_START

    def feed(self, minutes: int, **cells):
        """cells: cellN=value (constant). `minutes` of samples, then the clock is at the last sample."""
        steps = max(1, minutes // 5)
        values = {name.removeprefix("c"): [float(v)] * steps for name, v in cells.items()}
        pm(self.mesh, values, self.now)
        self.now += datetime.timedelta(minutes=5 * steps)
        return self


def evaluate(mesh, instance_id, correlation_id=None):
    headers = {"X-Correlation-ID": correlation_id} if correlation_id else {}
    return ok(mesh["energy-saving-rapp"].post(f"/instances/{instance_id}/evaluate", headers=headers))


def decision(result, cell):
    return next(d for d in result["decisions"] if d["cellId"] == cell)


def cell_state(mesh, instance_id, cell):
    return next(c for c in ok(mesh["energy-saving-rapp"].get(f"/instances/{instance_id}/cells"))["items"] if c["cellId"] == cell)


def o1(mesh, cell, ioc="NRCellDU"):
    return ok(mesh["mock-o1-adaptor"].get(f"/objects/{ME}", params={"function_ref": f"{ioc}={cell}"}))["attributes"]


def fault(mesh, cell, mode, count=1, ioc="NRCellDU"):
    ok(mesh["mock-o1-adaptor"].post("/faults", json={"mode": mode, "count": count, "managedObjectRef": f"{ME}/{ioc}={cell}"}))


def minutes(n):
    return datetime.timedelta(minutes=n)


def new_id():
    return str(uuid.uuid4())


def heartbeat(mesh, me):
    """The NF stays alive: RAN NF OAM marks an endpoint unreachable after 90 s
    of wall-clock silence, and a long scenario set-up can take that long."""
    for ep in ok(mesh["ran-nf-oam"].get("/o1-adaptor-endpoints", params={"limit": 500}))["items"]:
        if ep["managedElementRef"] == me:
            ok(mesh["ran-nf-oam"].post(f"/o1-adaptor-endpoints/{ep['endpointId']}/heartbeat"))
