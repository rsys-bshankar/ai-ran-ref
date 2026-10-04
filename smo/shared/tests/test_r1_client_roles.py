"""PR-SEC-14: R1Client's identity kind: an SMO module presents the enrollment secret and asks for `smo-internal`; an rApp presents none and asks for
`smo-rapp`, and keeps its identity under its own key."""

import pytest

from smo_shared import r1_client
from smo_shared.r1_client import R1Client, _ModuleIdentity

from test_r1_client import R1, SME, FakeNetwork  # noqa: F401


@pytest.fixture
def net(monkeypatch):
    fake = FakeNetwork()
    fake.sent = []                                         # (url, headers, json) of what goes to SME
    real_post = fake.post

    def post(url, json=None, headers=None, **kw):
        if url.startswith(SME):
            fake.sent.append((url, headers or {}, json))
        return real_post(url, json=json, headers=headers, **kw)

    monkeypatch.setattr(r1_client.httpx, "get", fake.get)
    monkeypatch.setattr(r1_client.httpx, "post", post)
    monkeypatch.setattr(r1_client, "_identity", _ModuleIdentity())
    for name in ("SMO_IDENTITY_KIND", "SMO_ENROLLMENT_SECRET", "SMO_ENROLLMENT_SECRET_FILE", "SMO_INVOKER_ID", "SMO_INVOKER_SECRET"):
        monkeypatch.delenv(name, raising=False)
    return fake


def _registration(net):
    return next(sent for sent in net.sent if sent[0].endswith("/invoker-registrations"))


def _grant(net):
    return next(sent for sent in net.sent if sent[0].endswith("/oauth2/token"))


def test_a_module_presents_the_enrollment_secret_and_asks_for_the_internal_scope(net, monkeypatch):
    monkeypatch.setenv("SMO_ENROLLMENT_SECRET", "the-secret")
    R1Client(R1).get("/sme/health")
    assert _registration(net)[1]["X-SMO-Enrollment"] == "the-secret"
    assert _registration(net)[2]["apiInvokerPublicKey"].startswith("smo-module:")
    assert _grant(net)[2]["scope"] == "smo-internal"


def test_the_secret_can_come_from_a_file(net, monkeypatch, tmp_path):
    path = tmp_path / "enrollment_secret"
    path.write_text("file-secret\n")
    monkeypatch.setenv("SMO_ENROLLMENT_SECRET_FILE", str(path))
    R1Client(R1).get("/sme/health")
    assert _registration(net)[1]["X-SMO-Enrollment"] == "file-secret"


def test_an_rapp_presents_nothing_even_if_it_somehow_has_the_secret(net, monkeypatch):
    monkeypatch.setenv("SMO_IDENTITY_KIND", "rapp")
    monkeypatch.setenv("SMO_ENROLLMENT_SECRET", "the-secret")
    R1Client(R1).get("/sme/health")
    assert "X-SMO-Enrollment" not in _registration(net)[1]
    assert _registration(net)[2]["apiInvokerPublicKey"].startswith("smo-rapp:")
    assert _grant(net)[2]["scope"] == "smo-rapp"


def test_a_process_without_the_secret_registers_without_the_header(net):
    R1Client(R1).get("/sme/health")
    assert "X-SMO-Enrollment" not in _registration(net)[1]


def test_an_rapps_identity_is_stored_under_its_own_key(net, monkeypatch):
    stored = {}

    class Store:
        def load(self, module):
            return stored.get(module)

        def insert(self, module, invoker_id, secret):
            stored.setdefault(module, (invoker_id, secret))
            return stored[module] == (invoker_id, secret)

    monkeypatch.setenv("MODULE", "samples/energy-saving-rapp")
    monkeypatch.setenv("SMO_IDENTITY_KIND", "rapp")
    r1_client._identity = _ModuleIdentity(store=Store())
    R1Client(R1).get("/sme/health")
    assert list(stored) == ["rapp:samples/energy-saving-rapp"]
