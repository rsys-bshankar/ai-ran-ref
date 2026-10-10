"""Pytest configuration of the shared unit tests: switches on the in-memory SQLite fallback of `smo_shared.db` before the application is imported.

Without a database URL `smo_shared.db` refuses to start unless `SMO_ALLOW_SQLITE_FALLBACK` is set; this file sets it for the whole suite. It defines one autouse fixture, `_no_real_dns`, so no test does a real host-name lookup.
Run the suite from `smo/shared` with `PYTHONPATH=.:../shared python -m pytest tests -q`.
"""

import os

# The in-memory SQLite fallback of smo_shared.db is an explicit opt-in (SMO_ALLOW_SQLITE_FALLBACK), never inferred from pytest being loaded; set here,
# before any application module is imported, because smo_shared.db builds its engine at import.
os.environ.setdefault("SMO_ALLOW_SQLITE_FALLBACK", "1")


import ipaddress
import socket

import pytest


@pytest.fixture(autouse=True)
def _no_real_dns(monkeypatch):
    """Makes a lookup of a host name that is neither an address nor `localhost` fail at once instead of asking a resolver, so no test depends on the network.

    The webhook guard resolves a destination's host name before it sends (`_resolves_to_blocked_address`); a name that does not resolve is attempted as before. Against a
    real resolver a lookup can take seconds on a CI runner, which made a mutant of the mutation job (scripts/mutation_pilot.sh) time out and so count as not killed. Addresses
    and `localhost` still go to the real resolver (the TLS probe tests connect to a local server). `socket.getaddrinfo` is one function for the whole process, so it is replaced
    for the length of each test only; a test that needs particular answers installs its own stub over this one (test_webhook.py, test_webhook_exact.py).
    """
    real = socket.getaddrinfo

    def guarded(host, *args, **kwargs):
        """The real lookup for an address or `localhost`; a name-not-found error for any other name."""
        text = host.decode() if isinstance(host, bytes) else str(host)
        try:
            ipaddress.ip_address(text.strip("[]"))
        except ValueError:
            if text.rstrip(".").lower() != "localhost":
                raise socket.gaierror(socket.EAI_NONAME, "no network in the unit tests") from None
        return real(host, *args, **kwargs)
    monkeypatch.setattr(socket, "getaddrinfo", guarded)
