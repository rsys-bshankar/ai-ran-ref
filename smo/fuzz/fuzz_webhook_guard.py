#!/usr/bin/python3
"""Fuzz target: the SSRF guard (smo_shared.webhook.is_safe_webhook_destination).

Contract checked on arbitrary input: it never raises, and it only ever allows an
http(s) URL whose host is not a blocked name or a loopback / link-local /
multicast / unspecified / reserved IP literal. Run by ClusterFuzzLite
(.clusterfuzzlite/); locally: python3 fuzz/fuzz_webhook_guard.py -max_total_time=30
"""
import ipaddress
import sys
from urllib.parse import urlsplit

import atheris

with atheris.instrument_imports():
    from smo_shared.webhook import is_safe_webhook_destination


def TestOneInput(data: bytes) -> None:
    """One fuzz iteration: turns `data` into a destination string and checks the guard's contract on it.

    The guard must return a bool without raising. When it allows the destination, the scheme must be http or https and the host
    must not be a blocked name or a loopback, link-local, multicast, unspecified or reserved IP literal; a failed `assert` is the
    finding atheris reports. A destination the guard refuses is not checked further.
    """
    fdp = atheris.FuzzedDataProvider(data)
    destination = fdp.ConsumeUnicodeNoSurrogates(200)

    allowed = is_safe_webhook_destination(destination)  # must not raise
    assert isinstance(allowed, bool)
    if not allowed:
        return

    parts = urlsplit(destination)
    assert parts.scheme in ("http", "https"), destination
    host = (parts.hostname or "").lower()
    assert host and host not in ("localhost", "metadata", "metadata.google.internal"), destination
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    assert not (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved), destination


def main() -> None:
    """Hands `TestOneInput` to atheris, which parses the fuzzer's command-line flags (such as -max_total_time) and runs until a finding or the limit."""
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
