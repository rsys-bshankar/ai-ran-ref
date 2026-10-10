"""DEMO_RUNBOOK section 7 (a real CM write) through the compose stack to a real NETCONF server over SSH (PR-SB-1.11).

Live mode only: `SMO_E2E_LIVE=1` and `SMO_LAB_HOST=netconf-lab` (the CI compose job). The stack's own RAN NF OAM holds the lab's password
(`netconf-lab/compose.replay.yml`) and is told the server's host key through its pin route, exactly as an operator would; nothing else is
shared with it. The in-process equivalent is `scripts/netconf_lab_runbook.py`.
"""

import os
import time

import pytest

from tests_integration import live

LAB = os.environ.get("SMO_LAB_HOST", "")
pytestmark = pytest.mark.skipif(not (live.LIVE and LAB), reason="needs the compose stack and the netconf-lab server (SMO_E2E_LIVE, SMO_LAB_HOST)")

ME = "SubNetwork=lab,ManagedElement=ME-1"
CELL = "GNBDUFunction=1,NRCellDU=101"


def _host_key(host: str, port: int = 830, wait: float = 120.0):
    """Connects to the lab SSH server, retrying every 2 seconds until `wait` seconds have passed, and returns the server's host key; the last error
    is raised when the time is up.
    """
    import paramiko

    deadline = time.monotonic() + wait
    while True:
        transport = None
        try:
            transport = paramiko.Transport((host, port))
            transport.start_client(timeout=10)
            return transport.get_remote_server_key()
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(2)
        finally:
            if transport is not None:
                transport.close()


def test_a_cm_write_reaches_the_lab_server_through_the_stack(mesh):
    """Runbook section 7 through the live stack to a real NETCONF server over SSH: the endpoint is refused until its host key is pinned through the
    route, and then a configuration write reaches the server. Live mode only.
    """
    oam = mesh["ran-nf-oam"]
    key = _host_key(LAB)

    reg = oam.post("/o1-adaptor-endpoints", json={
        "managedElementRef": ME, "adaptorUri": f"ssh://netconf@{LAB}:830?model=smo-lab&datastore=candidate", "transport": "ssh",
        "credentialRef": "lab", "protocolSupport": ["NETCONF"], "o1Protocol": "NETCONF", "entityType": "O-DU"})
    assert reg.status_code == 201, reg.text
    endpoint_id = reg.json()["endpointId"]

    # without a pinned key the server is refused: nothing is learned by connecting
    refused = oam.get(f"/managed-entities/{ME}/config", params={"managed_function_ref": CELL})
    assert refused.status_code != 200, refused.text

    pin = oam.put(f"/o1-adaptor-endpoints/{endpoint_id}/host-keys",
                  json={"keyType": key.get_name(), "publicKey": key.get_base64(), "pinnedBy": "compose-replay"})
    assert pin.status_code == 200, pin.text

    before = oam.get(f"/managed-entities/{ME}/config", params={"managed_function_ref": CELL})
    assert before.status_code == 200, before.text
    assert before.json()["attributes"]["txPower"] == "40"

    def write(value):
        r = oam.post("/config-jobs", json={"requestedBy": "energy-saving-rapp", "scope": "cell", "changes": [
            {"managedElementRef": ME, "managedFunctionRef": CELL, "attributeChanges": {"txPower": value}}]})
        assert r.status_code == 202, r.text
        return oam.get(f"/config-jobs/{r.json()['jobId']}").json()

    try:
        job = write(41)
        assert job["status"] == "COMPLETED" and job["subChanges"][0]["status"] == "APPLIED", job
        after = oam.get(f"/managed-entities/{ME}/config", params={"managed_function_ref": CELL}).json()["attributes"]
        assert after["txPower"] == "41"

        refused_write = write(99)    # outside the model's range: the server's own reason comes back
        sub = refused_write["subChanges"][0]
        assert sub["status"] == "REJECTED" and sub["rejectionReason"] == "NETCONF_RPC_FAILED" and sub["rejectionDetail"], refused_write
        assert oam.get(f"/managed-entities/{ME}/config", params={"managed_function_ref": CELL}).json()["attributes"]["txPower"] == "41"
    finally:
        write(40)    # leave the lab as it was seeded
