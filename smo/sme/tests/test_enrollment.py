"""PR-SEC-14: an invoker is an SMO module's (it presented the enrollment secret) or an rApp's (it did not); token introspection says which as `role`, and an rApp is not
granted the scopes the SMO's own clients use (`smo-internal`, `smo-gui`). Covers `_enrolled`, `_check_scope` and `introspect_token` of `app/main.py`.

Fixtures: `client` and `db_session_factory` are imported from `test_main.py` (SQLite, TestClient); `secured` (here) takes away the open enrollment that `conftest.py` turns on.

Run: `cd smo/sme && PYTHONPATH=.:../shared python -m pytest tests/test_enrollment.py -q`. Needs nothing external.
"""

import pytest

from test_main import client, db_session_factory  # noqa: F401  (pytest fixtures)

SECRET = "enrollment-secret-of-the-test-stack"
ENROLL = {"X-SMO-Enrollment": SECRET}


@pytest.fixture
def secured(monkeypatch):
    """Configures the enrollment secret `SECRET` and turns open enrollment off, so the test sees the real rule: the header decides the invoker's kind."""
    monkeypatch.delenv("SME_ALLOW_OPEN_ENROLLMENT", raising=False)
    monkeypatch.setenv("SMO_ENROLLMENT_SECRET", SECRET)


def _register(client, headers=None):
    """Onboards an invoker with an opaque-label public key and optional headers (for example the enrollment header); returns the raw response."""
    return client.post("/invoker-registrations", json={"apiInvokerPublicKey": "label"}, headers=headers or {})


def _token(client, registration, scope=None):
    """Requests a token with the registration's secret (optionally for `scope`) and returns the raw response."""
    body = {"grant_type": "client_credentials", "client_id": registration["apiInvokerId"], "client_secret": registration["onboardingSecret"]}
    if scope:
        body["scope"] = scope
    return client.post("/oauth2/token", json=body)


def test_the_enrollment_secret_makes_an_invoker_internal_and_none_makes_it_an_rapp(client, secured):
    """With a secret configured, presenting it gives role `internal` and presenting nothing gives `rapp`."""
    assert _register(client, ENROLL).json()["role"] == "internal"
    assert _register(client).json()["role"] == "rapp"


def test_a_wrong_enrollment_secret_is_refused_not_downgraded(client, secured):
    """A wrong secret is 403 `ENROLLMENT_REFUSED` and registers nothing; it must not quietly become an rApp, because a module with the wrong secret is a misconfiguration to notice."""
    resp = _register(client, {"X-SMO-Enrollment": "not-the-secret"})
    assert resp.status_code == 403 and resp.json()["detail"]["title"] == "ENROLLMENT_REFUSED"
    assert client.get("/invoker-registrations").json()["items"] == []


def test_without_a_secret_sme_does_not_guess(client, monkeypatch):
    """With no secret configured and open enrollment off, registration is 503 `ENROLLMENT_NOT_CONFIGURED`: SME cannot tell a module from an rApp."""
    monkeypatch.delenv("SME_ALLOW_OPEN_ENROLLMENT", raising=False)
    resp = _register(client)
    assert resp.status_code == 503 and resp.json()["detail"]["title"] == "ENROLLMENT_NOT_CONFIGURED"


def test_open_enrollment_makes_everyone_internal_as_before(client):
    """With open enrollment on (development and the unit tests), everyone is `internal`, as before roles existed."""
    assert _register(client).json()["role"] == "internal"          # the conftest turns it on


def test_an_rapp_is_not_granted_an_internal_scope(client, secured):
    """An rApp asking for `smo-internal` or `smo-gui` gets 400 `invalid_scope`, while `smo-rapp` and no scope (the CAPIF flows) are granted."""
    rapp = _register(client).json()
    for scope in ("smo-internal", "smo-gui"):
        resp = _token(client, rapp, scope)
        assert resp.status_code == 400 and resp.json()["error"] == "invalid_scope"
    assert _token(client, rapp, "smo-rapp").status_code == 200
    assert _token(client, rapp).status_code == 200                  # no scope: the CAPIF flows


def test_an_internal_invoker_gets_every_scope(client, secured):
    """An internal invoker is granted `smo-internal`, `smo-gui` and `smo-rapp`."""
    module = _register(client, ENROLL).json()
    for scope in ("smo-internal", "smo-gui", "smo-rapp"):
        assert _token(client, module, scope).status_code == 200


def test_in_audit_mode_an_rapp_is_granted_the_scope_and_it_is_logged(client, secured, monkeypatch, caplog):
    """With `SMO_ROLE_ENFORCEMENT=audit` the rApp gets the internal scope anyway (rolling upgrade) and a 'role audit' warning is logged."""
    monkeypatch.setenv("SMO_ROLE_ENFORCEMENT", "audit")
    rapp = _register(client).json()
    with caplog.at_level("WARNING"):
        assert _token(client, rapp, "smo-internal").status_code == 200
    assert "role audit" in caplog.text


def test_a_mistyped_enforcement_mode_is_enforce(client, secured, monkeypatch):
    """An unrecognised `SMO_ROLE_ENFORCEMENT` value behaves as `enforce`, so a typo cannot switch the protection off."""
    monkeypatch.setenv("SMO_ROLE_ENFORCEMENT", "audti")
    assert _token(client, _register(client).json(), "smo-internal").status_code == 400


def test_introspection_reports_the_role(client, secured):
    """Introspection returns the invoker's role (`internal` or `rapp`) and its id as `client_id`; R1 Termination applies its role policy on it."""
    for headers, role in ((ENROLL, "internal"), ({}, "rapp")):
        registration = _register(client, headers).json()
        token = _token(client, registration, "smo-rapp").json()["access_token"]
        view = client.post("/oauth2/introspect", json={"token": token}).json()
        assert view["active"] is True and view["role"] == role and view["client_id"] == registration["apiInvokerId"]
