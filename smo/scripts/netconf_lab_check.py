#!/usr/bin/env python3
"""Check the netconf-lab server with the SSH session wrapper of RAN NF OAM (PR-SB-1.3 and 1.4): connect, exchange <hello>, read the lab
model's data with <get-config>. Exit 0 only when the seeded values come back. See netconf-lab/README.md.

    NETCONF_SSH_KNOWN_HOSTS=... NETCONF_SSH_PASSWORD=netconf PYTHONPATH=ran-nf-oam:shared python scripts/netconf_lab_check.py [ssh://netconf@127.0.0.1:8830]
"""

import sys
import xml.etree.ElementTree as ET

from app.netconf_ssh import BASE_10, BASE_11, NetconfSession, NetconfSshError

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
