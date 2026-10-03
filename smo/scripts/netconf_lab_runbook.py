#!/usr/bin/env python3
"""Runbook section 7 (a real CM write) against the netconf-lab server, through RAN NF OAM's own routes (PR-SB-1.9).

The compose replay (`tests_integration/test_demo_runbook.py`) drives the mock O1 adaptor. This drives the same routes, in this process, over
NETCONF-over-SSH to Netopeer2 with the model payload and the candidate datastore: register the adaptor (with its own credential, `credentialRef: lab`), heartbeat it, write a value, read it back,
look at the change history, run a batch with one ME that was never registered (PARTIAL_SUCCESS), and have a value outside the model's range
refused with the server's own reason. It puts the seeded value back at the end. Exit 0 only when every step holds.

    NETCONF_SSH_KNOWN_HOSTS=... NETCONF_CRED_LAB_PASSWORD=netconf PYTHONPATH=ran-nf-oam:shared python scripts/netconf_lab_runbook.py [host:port]
"""

import os
import sys

os.environ.setdefault("SMO_DATABASE_URL", "sqlite://")        # the module needs a URL to import; this script uses its own in-memory engine

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from smo_shared import outbox  # noqa: F401  (registers the outbox table on the metadata)
from smo_shared.db import Base, get_session
from smo_shared.idempotency import IdempotencyKey
from smo_shared.outbox import NotificationOutbox
from smo_shared.testing import make_test_engine

from app.main import app
from app.models import (Alarm, CMSchemaCache, CMSnapshot, ManagedEntity, MsacAccessRule, MsacIdentity, MsacRole, O1AdaptorEndpoint,
                        VendorCapability, WriteConfigJob, WriteConfigSubChange)

ME = "SubNetwork=lab,ManagedElement=ME-1"
CELL = "GNBDUFunction=1,NRCellDU=101"
failures: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f" -- {detail}"))
    if not ok:
        failures.append(name)


def main() -> int:
    hostport = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1:8830"
    engine = make_test_engine()
    Base.metadata.create_all(engine, tables=[
        O1AdaptorEndpoint.__table__, ManagedEntity.__table__, Alarm.__table__, CMSchemaCache.__table__, WriteConfigJob.__table__,
        WriteConfigSubChange.__table__, CMSnapshot.__table__, VendorCapability.__table__, MsacIdentity.__table__, MsacRole.__table__,
        MsacAccessRule.__table__, IdempotencyKey.__table__, NotificationOutbox.__table__])
    factory = sessionmaker(bind=engine)

    def session():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = session
    client = TestClient(app)

    def write(changes):
        r = client.post("/config-jobs", json={"requestedBy": "energy-saving-rapp", "scope": "cell", "changes": changes})
        job = client.get(f"/config-jobs/{r.json()['jobId']}").json() if r.status_code == 202 else {}
        return r, job

    r = client.post("/o1-adaptor-endpoints", json={
        "managedElementRef": ME, "adaptorUri": f"ssh://netconf@{hostport}?model=smo-lab&datastore=candidate", "transport": "ssh", "credentialRef": "lab",
        "protocolSupport": ["NETCONF"], "o1Protocol": "NETCONF", "entityType": "O-DU"})
    check("register the adaptor (ssh, model, candidate)", r.status_code == 201, r.text)
    r = client.post(f"/o1-adaptor-endpoints/{r.json()['endpointId']}/heartbeat")
    check("heartbeat: ACTIVE", r.status_code == 200 and r.json().get("healthStatus") == "ACTIVE", r.text)

    before = client.get(f"/managed-entities/{ME}/config", params={"managed_function_ref": CELL}).json()["attributes"]
    check("read cell 101 before the write", before.get("txPower") == "40", before)

    r, job = write([{"managedElementRef": ME, "managedFunctionRef": CELL, "attributeChanges": {"txPower": 41}}])
    sub = (job.get("subChanges") or [{}])[0]
    check("CM write: COMPLETED, sub-change APPLIED", job.get("status") == "COMPLETED" and sub.get("status") == "APPLIED", (r.text, job))
    after = client.get(f"/managed-entities/{ME}/config", params={"managed_function_ref": CELL}).json()["attributes"]
    check("the write is in the server: txPower 41", after.get("txPower") == "41", after)
    history = client.get(f"/managed-entities/{ME}/config-history", params={"managed_function_ref": CELL}).json()["items"]
    check("history: before 40, after 41", bool(history) and history[0]["before"] == {"txPower": "40"} and history[0]["after"] == {"txPower": 41}, history[:1])

    r, job = write([{"managedElementRef": ME, "managedFunctionRef": CELL, "attributeChanges": {"txPower": 40}},
                    {"managedElementRef": "demo-o-du-2-never-registered", "attributeChanges": {"adminState": "LOCKED"}}])
    reasons = sorted((s["status"], s["rejectionReason"]) for s in job.get("subChanges", []))
    check("batch with an unregistered ME: PARTIAL_SUCCESS", job.get("status") == "PARTIAL_SUCCESS"
          and reasons == [("APPLIED", None), ("REJECTED", "ENDPOINT_UNREACHABLE")], (r.text, job))

    r, job = write([{"managedElementRef": ME, "managedFunctionRef": CELL, "attributeChanges": {"txPower": 99}}])
    sub = (job.get("subChanges") or [{}])[0]
    check("a value outside the model's range is REJECTED with the server's reason",
          sub.get("status") == "REJECTED" and sub.get("rejectionReason") == "NETCONF_RPC_FAILED" and bool(sub.get("rejectionDetail")), (r.text, job))
    print("  server said:", sub.get("rejectionDetail"))
    final = client.get(f"/managed-entities/{ME}/config", params={"managed_function_ref": CELL}).json()["attributes"]
    check("the refused write left the value as it was (40)", final.get("txPower") == "40", final)

    print("FAILED: " + ", ".join(failures) if failures else "OK: runbook section 7 holds against the lab server")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
