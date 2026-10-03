#!/usr/bin/env python3
"""Check the netconf-lab server with the SSH session wrapper of RAN NF OAM (PR-SB-1.3 and 1.4): connect, exchange <hello>, read the lab
model's data with <get-config>. Exit 0 only when the seeded values come back. See netconf-lab/README.md.

    NETCONF_SSH_KNOWN_HOSTS=... NETCONF_SSH_PASSWORD=netconf [NETCONF_LAB_TLS_URI=... NETCONF_CRED_LABTLS_*=...] PYTHONPATH=ran-nf-oam:shared python scripts/netconf_lab_check.py [ssh://netconf@127.0.0.1:8830]
"""

import os
import sys
import xml.etree.ElementTree as ET

from app.netconf_ssh import BASE_10, BASE_11, NetconfSession, NetconfSshError, send_edit_config, send_get_config, send_walk

NS = "urn:ietf:params:xml:ns:netconf:base:1.0"
GET_LAB = (f'<rpc message-id="lab-1" xmlns="{NS}"><get-config><source><running/></source>'
           '<filter type="subtree"><lab xmlns="urn:smo:lab"/></filter></get-config></rpc>')


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def main() -> int:
    uri = sys.argv[1] if len(sys.argv) > 1 else "ssh://netconf@127.0.0.1:8830"
    try:
        with NetconfSession(uri, timeout=30) as session:
            caps = session.server_capabilities
            print(f"hello: {len(caps)} capabilities, framing {'chunked (base:1.1)' if session.chunked else 'end-of-message (base:1.0)'}")
            if BASE_10 not in caps and BASE_11 not in caps:
                print("FAIL: no base capability in the server's hello", file=sys.stderr)
                return 1
            # A YANG 1.1 module (smo-lab is one) is listed in ietf-yang-library, not in the hello (RFC 7950 section 5.6.4), so
            # this only reports; the proof that the model is installed is the data that comes back below.
            print("smo-lab advertised in the hello:", any("urn:smo:lab" in c for c in caps))
            reply = session.rpc(GET_LAB)
    except NetconfSshError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(ET.tostring(reply, encoding="unicode"))
    cells = {}
    for node in reply.iter():
        if local(node.tag) == "cell":
            values = {local(child.tag): (child.text or "").strip() for child in node}
            cells[values.get("id")] = values
    want = {"101": {"administrative-state": "unlocked", "tx-power": "40"}, "102": {"administrative-state": "locked", "tx-power": "33"}}
    for cell_id, expected in want.items():
        got = cells.get(cell_id, {})
        if any(got.get(k) != v for k, v in expected.items()):
            print(f"FAIL: cell {cell_id}: expected {expected}, got {got}", file=sys.stderr)
            return 1
    print("OK: get-config returned the seeded cells")
    # PR-SB-1.5: the same read the way the /managed-entities/{ref}/config route makes it, with ?model=smo-lab
    attributes = send_get_config(uri + "?model=smo-lab", "SubNetwork=lab,ManagedElement=ME-1", "lab-2", "GNBDUFunction=1,NRCellDU=102")
    print("route-shaped read of cell 102:", attributes)
    if attributes != {"administrativeState": "locked", "txPower": "33"}:
        print("FAIL: the model-based read did not return cell 102's seeded values", file=sys.stderr)
        return 1
    print("OK: the route's read (send_get_config with ?model=smo-lab) returns cell 102")
    # PR-SB-1.6/1.7: a write the way the route makes it, read back, put back; and a value outside the model's range, refused with the
    # server's own reason
    model_uri, me, function = uri + "?model=smo-lab", "SubNetwork=lab,ManagedElement=ME-1", "GNBDUFunction=1,NRCellDU=101"
    done = send_edit_config(model_uri, me, {"txPower": 41}, "lab-3", "merge", function)
    print("edit-config tx-power 41 on cell 101:", done.applied, done.reason, done.detail)
    if not done.applied:
        print("FAIL: the lab server did not apply a valid write", file=sys.stderr)
        return 1
    after = send_get_config(model_uri, me, "lab-4", function)
    send_edit_config(model_uri, me, {"txPower": 40}, "lab-5", "merge", function)   # put the seeded value back
    if not after or after.get("txPower") != "41":
        print(f"FAIL: the write is not in the running datastore: {after}", file=sys.stderr)
        return 1
    refused = send_edit_config(model_uri, me, {"txPower": 99}, "lab-6", "merge", function)
    print("edit-config tx-power 99 on cell 101:", refused.applied, refused.reason, refused.detail)
    if refused.applied or refused.reason != "NETCONF_RPC_FAILED" or not refused.detail:
        print("FAIL: an out-of-range value was not refused with a reason", file=sys.stderr)
        return 1
    print("OK: a valid write is applied and read back, an out-of-range one is refused with the server's detail")
    # PR-SB-6.2: a walk of the model's whole container, as the containment tree is filled from it
    paths = send_walk(model_uri, "lab-walk")
    print("walk of the lab server:", paths)
    if paths != ["GNBDUFunction=1,NRCellDU=101", "GNBDUFunction=1,NRCellDU=102"]:
        print("FAIL: the walk did not report the two seeded cells", file=sys.stderr)
        return 1
    print("OK: the walk reports both seeded cells as DNs below the element root")
    return check_tls(me, function)


def check_tls(me: str, function: str) -> int:
    """PR-SB-2.5: the same read, write and refusal over NETCONF-over-TLS with a client certificate, when NETCONF_LAB_TLS_URI names the lab's
    TLS listener (tls://127.0.0.1:6513?model=smo-lab) and the endpoint's credential `labtls` is configured (NETCONF_CRED_LABTLS_CERT_FILE,
    _KEY_FILE, _CA_FILE: the files the lab wrote to netconf-lab/pki)."""
    uri = os.environ.get("NETCONF_LAB_TLS_URI")
    if not uri:
        print("(no NETCONF_LAB_TLS_URI: the TLS listener is not checked)")
        return 0
    read = send_get_config(uri, me, "tls-1", function, credential_ref="labtls")
    print("TLS read of cell 101:", read)
    if not read or read.get("txPower") != "40":
        print("FAIL: the TLS read did not return cell 101's seeded values", file=sys.stderr)
        return 1
    done = send_edit_config(uri, me, {"txPower": 42}, "tls-2", "merge", function, credential_ref="labtls")
    after = send_get_config(uri, me, "tls-3", function, credential_ref="labtls")
    send_edit_config(uri, me, {"txPower": 40}, "tls-4", "merge", function, credential_ref="labtls")                 # put the seeded value back
    print("TLS edit-config tx-power 42:", done.applied, done.reason, done.detail, "| read back:", after)
    if not done.applied or not after or after.get("txPower") != "42":
        print("FAIL: the TLS write was not applied and read back", file=sys.stderr)
        return 1
    refused = send_edit_config(uri, me, {"txPower": 99}, "tls-5", "merge", function, credential_ref="labtls")
    print("TLS edit-config tx-power 99:", refused.applied, refused.reason, refused.detail)
    if refused.applied or refused.reason != "NETCONF_RPC_FAILED" or not refused.detail:
        print("FAIL: an out-of-range value over TLS was not refused with a reason", file=sys.stderr)
        return 1
    print("OK: over TLS with a client certificate, a read, a write and a refusal behave as over SSH")
    return 0


if __name__ == "__main__":
    sys.exit(main())
