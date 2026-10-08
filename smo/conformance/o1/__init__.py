"""The O1 adaptor conformance kit (PR-SB-9): what RAN NF OAM relies on when it talks to an adaptor, as checks you can run against one.

    python -m conformance.o1 --adaptor http://adaptor:8000 [--protocol auto|netconf|restconf|both] [--out report]

It checks the contract RAN NF OAM's clients use (`ran-nf-oam/app/netconf_client.py`, `restconf_client.py`, the vendor discovery at `/capabilities`), not the whole of RFC 6241 or
RFC 8040: a request RAN NF OAM never makes is not tested. See `conformance/README.md` for the list.
"""
