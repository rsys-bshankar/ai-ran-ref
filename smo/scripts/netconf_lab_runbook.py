#!/usr/bin/env python3
"""Runbook section 7 (a real CM write) against the netconf-lab server, through RAN NF OAM's own routes (PR-SB-1.9).

The compose replay (`tests_integration/test_demo_runbook.py`) drives the mock O1 adaptor. This drives the same routes, in this process, over
NETCONF-over-SSH to Netopeer2 with the model payload and the candidate datastore: register the adaptor (with its own credential, `credentialRef: lab`), pin its host key through the route, heartbeat it, write a value, read it back,
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
from app.models import (ManagedObject, Alarm, CMSchemaCache, CMSnapshot, ManagedEntity, MsacAccessRule, MsacIdentity, MsacRole, O1AdaptorEndpoint,
                        O1AdaptorHostKey, OnboardingTemplate, ElementOnboarding, LifecycleSubscription, VendorCapability, WriteConfigJob, WriteConfigSubChange)

ME = "SubNetwork=lab,ManagedElement=ME-1"
CELL = "GNBDUFunction=1,NRCellDU=101"
failures: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    """Prints one line per step (`ok` or `FAIL` with the detail) and records a failed step's name in `failures`, which decides the exit code."""
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f" -- {detail}"))
    if not ok:
        failures.append(name)


def main() -> int:
    """Runs runbook section 7 against the lab NETCONF server and returns the process exit code (0 when every step holds, 1 otherwise).

    Builds an in-memory RAN NF OAM (its own SQLite engine and the route app through TestClient); `argv[1]` is the server's host:port (default
    127.0.0.1:8830). It pins the server's host key from the file `NETCONF_SSH_KNOWN_HOSTS` names and then removes that variable from the
    environment, so later steps prove the pinned key is what establishes trust. It writes values to the server and puts the seeded ones back.
    """
    hostport = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1:8830"
    engine = make_test_engine()
    Base.metadata.create_all(engine, tables=[
        O1AdaptorEndpoint.__table__, ManagedEntity.__table__, Alarm.__table__, CMSchemaCache.__table__, WriteConfigJob.__table__,
        WriteConfigSubChange.__table__, CMSnapshot.__table__, VendorCapability.__table__, MsacIdentity.__table__, MsacRole.__table__,
        MsacAccessRule.__table__, IdempotencyKey.__table__, NotificationOutbox.__table__, ManagedObject.__table__, O1AdaptorHostKey.__table__,
        OnboardingTemplate.__table__, ElementOnboarding.__table__, LifecycleSubscription.__table__])         # registering an element looks for an onboarding template, and a failed onboarding for its subscribers (PR-MGT-14)
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
    endpoint_id = r.json().get("endpointId")
    # PR-SB-2.3: trust comes from a key an operator pins for this endpoint, not from a known_hosts file: take the key from the file the CI job
    # recorded (standing in for the operator's out-of-band source), pin it through the route, then drop the file from this process
    known = os.environ.pop("NETCONF_SSH_KNOWN_HOSTS", "")
    fields = open(known).read().split() if known else []
    pin = client.put(f"/o1-adaptor-endpoints/{endpoint_id}/host-keys",
                     json={"keyType": fields[-2], "publicKey": fields[-1], "pinnedBy": "runbook"}) if len(fields) >= 3 else None
    check("pin the server's host key through the route (no known_hosts file from here on)", pin is not None and pin.status_code == 200, pin and pin.text)
    print("  pinned:", pin.json().get("fingerprint") if pin is not None and pin.status_code == 200 else None)
    r = client.post(f"/o1-adaptor-endpoints/{endpoint_id}/heartbeat")
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

    # PR-SB-1.10: the sub-changes of one element are one candidate transaction against the real server: both take effect, or neither
    cell2 = "GNBDUFunction=1,NRCellDU=102"
    r, job = write([{"managedElementRef": ME, "managedFunctionRef": CELL, "attributeChanges": {"txPower": 41}},
                    {"managedElementRef": ME, "managedFunctionRef": cell2, "attributeChanges": {"txPower": 34}}])
    check("two cells in one job: both APPLIED", job.get("status") == "COMPLETED" and [s["status"] for s in job.get("subChanges", [])] == ["APPLIED", "APPLIED"], (r.text, job))
    values = [client.get(f"/managed-entities/{ME}/config", params={"managed_function_ref": c}).json()["attributes"].get("txPower") for c in (CELL, cell2)]
    check("both values are in the server (41, 34)", values == ["41", "34"], values)
    r, job = write([{"managedElementRef": ME, "managedFunctionRef": CELL, "attributeChanges": {"txPower": 40}},
                    {"managedElementRef": ME, "managedFunctionRef": cell2, "attributeChanges": {"txPower": 33}}])
    check("restored: 40 and 33", job.get("status") == "COMPLETED", (r.text, job))
    r, job = write([{"managedElementRef": ME, "managedFunctionRef": CELL, "attributeChanges": {"txPower": 41}},
                    {"managedElementRef": ME, "managedFunctionRef": cell2, "attributeChanges": {"txPower": 99}}])
    subs = job.get("subChanges", [])
    check("a refused second sub-change rejects the first too (ABORTED), with the server's reason on the second",
          job.get("status") == "FAILED" and [x.get("rejectionReason") for x in subs] == ["NETCONF_TRANSACTION_ABORTED", "NETCONF_RPC_FAILED"]
          and bool(subs[1].get("rejectionDetail")), (r.text, job))
    print("  server said:", subs[1].get("rejectionDetail") if len(subs) > 1 else None)
    left = client.get(f"/managed-entities/{ME}/config", params={"managed_function_ref": CELL}).json()["attributes"]
    check("the first change was not left committed: cell 101 is still 40", left.get("txPower") == "40", left)

    # PR-SB-6: fill the containment tree from the server, read it, refuse a target that is not in it, export it
    walked = client.post(f"/managed-entities/{ME}/managed-objects/refresh").json()
    check("walk: the tree gains the function and both cells", walked.get("added") == 3, walked)
    kids = [o["id"] for o in client.get(f"/managed-objects/{ME},GNBDUFunction=1/children").json().get("items", [])]    # ME is itself a DN
    check("children of GNBDUFunction=1: cells 101 and 102", kids == ["101", "102"], kids)
    os.environ["RAN_NF_OAM_ENFORCE_MO_TREE"] = "true"
    r, job = write([{"managedElementRef": ME, "managedFunctionRef": "GNBDUFunction=1,NRCellDU=999", "attributeChanges": {"txPower": 41}}])
    sub = (job.get("subChanges") or [{}])[0]
    check("enforced: a cell that is not in the tree is REJECTED before anything is sent",
          sub.get("rejectionReason") == "MANAGED_OBJECT_NOT_FOUND" and sub.get("attempts") == 0, (r.text, job))
    r, job = write([{"managedElementRef": ME, "managedFunctionRef": CELL, "attributeChanges": {"txPower": 40}}])
    sub = (job.get("subChanges") or [{}])[0]
    check("enforced: a walked cell is written", sub.get("status") == "APPLIED", (r.text, job))
    os.environ.pop("RAN_NF_OAM_ENFORCE_MO_TREE")
    topology = client.get("/topology").json()
    nodes = topology["entities"][0]["o-ran-smo-teiv-ran:ManagedObject"]
    links = topology["relationships"][0]["o-ran-smo-teiv-ran:MANAGEDOBJECT_CHILD_OF_MANAGEDOBJECT"]
    check("TEIV export: five nodes (SubNetwork, element, function, two cells) and four parent links", len(nodes) == 5 and len(links) == 4, topology)

    print("FAILED: " + ", ".join(failures) if failures else "OK: runbook section 7 holds against the lab server")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
