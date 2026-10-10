"""Every dispatched sub-change now reads its before image first (MGT-1.2). Unit tests have no adaptor at the registered URIs, so by
default that read answers an empty object at once instead of trying the network; a test that cares patches it again."""

import pytest


@pytest.fixture(autouse=True)
def _no_network_before_image(monkeypatch):
    """Autouse fixture: makes the before-image read (NETCONF and RESTCONF) answer an empty object at once, so no test tries the network at a registered adaptor URI. A test that cares patches it again.
    """
    monkeypatch.setattr("app.main.send_get_config", lambda *a, **kw: {})
    monkeypatch.setattr("app.main.restconf_client.send_get", lambda *a, **kw: {})
