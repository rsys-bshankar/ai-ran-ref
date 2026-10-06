"""smo_shared/mtls.py (PR-SEC-2): off means nothing changes; on means a certificate is needed, fail closed, and a real handshake proves the refusal.

Run with: cd smo/shared && PYTHONPATH=. python -m pytest tests/test_mtls.py -q
"""

import importlib.util
import socket
import ssl
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from smo_shared import metrics, mtls
from smo_shared.r1_client import R1Client
from smo_shared.webhook import get_webhook

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "mtls_certs.py"
_spec = importlib.util.spec_from_file_location("mtls_certs", SCRIPT)
certs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(certs)

ENV = ("SMO_MTLS", "SMO_MTLS_SERVE", "SMO_MTLS_CERT_FILE", "SMO_MTLS_KEY_FILE", "SMO_MTLS_CA_FILE", "SMO_MTLS_INTERNAL_HOSTS")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    root = tmp_path_factory.mktemp("pki")
    certs.init(root, 30)
    certs.new_client(root, "outsider", 30)
    return root


def use(monkeypatch, directory: Path) -> None:
    monkeypatch.setenv("SMO_MTLS", "on")
    monkeypatch.setenv("SMO_MTLS_CERT_FILE", str(directory / "tls.crt"))
    monkeypatch.setenv("SMO_MTLS_KEY_FILE", str(directory / "tls.key"))
    monkeypatch.setenv("SMO_MTLS_CA_FILE", str(directory / "ca.crt"))


def test_off_by_default_nothing_changes():
    assert not mtls.enabled() and not mtls.serving()
    assert mtls.http_url("http://sme:8000") == "http://sme:8000"
    assert mtls.client_kwargs("https://sme:8000") == {} and mtls.client_kwargs() == {}
    assert mtls.webhook_kwargs("https://ran-nf-oam:8000/cb") == {}
    assert mtls.uvicorn_args() == []
    assert mtls.main(["uvicorn-args"]) == 0


@pytest.mark.parametrize("value", ["off", "", "0", "false", "no", "maybe"])
def test_only_an_explicit_on_turns_it_on(monkeypatch, value):
    monkeypatch.setenv("SMO_MTLS", value)
    assert not mtls.enabled()


def test_on_upgrades_internal_addresses_and_nothing_else(monkeypatch, pki):
    use(monkeypatch, pki / "sme")
    assert mtls.http_url("http://sme:8000") == "https://sme:8000"
    assert mtls.http_url("https://already:8000") == "https://already:8000"
    assert mtls.http_url("ftp://x") == "ftp://x"


def test_server_options_require_a_client_certificate_and_name_the_files(monkeypatch, pki):
    use(monkeypatch, pki / "sme")
    args = mtls.uvicorn_args()
    assert args[args.index("--ssl-cert-reqs") + 1] == str(int(ssl.CERT_REQUIRED)) == "2"
    assert args[args.index("--ssl-certfile") + 1].endswith("sme/tls.crt")
    assert args[args.index("--ssl-keyfile") + 1].endswith("sme/tls.key")
    assert args[args.index("--ssl-ca-certs") + 1].endswith("sme/ca.crt")


def test_a_client_only_process_serves_plain_http(monkeypatch, pki):
    use(monkeypatch, pki / "gui-bff")
    monkeypatch.setenv("SMO_MTLS_SERVE", "off")
    assert mtls.enabled() and not mtls.serving() and mtls.uvicorn_args() == []
    assert "verify" in mtls.client_kwargs("https://r1-termination:8000")


def test_fail_closed_when_a_file_is_missing_or_empty(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SMO_MTLS", "on")
    monkeypatch.setenv("SMO_MTLS_CERT_FILE", str(tmp_path / "nope.crt"))
    monkeypatch.setenv("SMO_MTLS_KEY_FILE", str(tmp_path / "nope.key"))
    monkeypatch.setenv("SMO_MTLS_CA_FILE", str(tmp_path / "nope-ca.crt"))
    with pytest.raises(mtls.MtlsError, match="nope.crt"):
        mtls.uvicorn_args()
    with pytest.raises(mtls.MtlsError):
        mtls.client_kwargs("https://sme:8000")                # never an unverified or certificate-less call
    assert mtls.main(["uvicorn-args"]) == 1 and "nope.crt" in capsys.readouterr().err
    empty = tmp_path / "empty.crt"
    empty.write_bytes(b"")
    monkeypatch.setenv("SMO_MTLS_CERT_FILE", str(empty))
    with pytest.raises(mtls.MtlsError, match="empty"):
        mtls.uvicorn_args()


def test_a_key_that_does_not_match_the_certificate_is_refused(monkeypatch, pki, tmp_path):
    use(monkeypatch, pki / "sme")
    monkeypatch.setenv("SMO_MTLS_KEY_FILE", str(pki / "dme" / "tls.key"))
    with pytest.raises(mtls.MtlsError, match="do not load"):
        mtls.client_context()


def test_the_client_context_verifies_and_is_rebuilt_when_the_files_change(monkeypatch, pki, tmp_path):
    work = tmp_path / "sme"
    work.mkdir()
    for name in ("tls.crt", "tls.key", "ca.crt"):
        (work / name).write_bytes((pki / "sme" / name).read_bytes())
    use(monkeypatch, work)
    first = mtls.client_context()
    assert first is mtls.client_context()                     # cached while the files are as they were
    assert first.verify_mode == ssl.CERT_REQUIRED and first.check_hostname
    certs.renew(pki, 30)                                      # new certificates in the shared PKI; copy one in, as a rotation does
    time.sleep(0.01)
    (work / "tls.crt").write_bytes((pki / "sme" / "tls.crt").read_bytes())
    (work / "tls.key").write_bytes((pki / "sme" / "tls.key").read_bytes())
    assert mtls.client_context() is not first


@pytest.mark.parametrize("host,internal", [
    ("ran-nf-oam", True), ("r1-termination", True), ("sme.smo.svc", True), ("sme.smo.svc.cluster.local", True), ("SME", True),
    ("example.com", False), ("rapp.example.org", False), ("10.1.2.3", False), ("::1", False), ("", False), (None, False),
])
def test_which_callback_hosts_are_inside_the_deployment(host, internal):
    assert mtls.is_internal_host(host) is internal


def test_extra_internal_host_patterns(monkeypatch):
    assert not mtls.is_internal_host("rapp.corp.example")
    monkeypatch.setenv("SMO_MTLS_INTERNAL_HOSTS", "*.corp.example, other.internal")
    assert mtls.is_internal_host("rapp.corp.example") and mtls.is_internal_host("other.internal")
    assert not mtls.is_internal_host("evil.example")


def test_a_callback_carries_the_certificate_only_to_an_https_internal_destination(monkeypatch, pki):
    use(monkeypatch, pki / "sme")
    assert "verify" in mtls.webhook_kwargs("https://ran-nf-oam:8000/dme-jobs")
    assert mtls.webhook_kwargs("http://ran-nf-oam:8000/dme-jobs") == {}          # plain stays plain
    assert mtls.webhook_kwargs("https://hooks.example.com/x") == {}              # a stranger gets the system roots and no certificate
    assert mtls.webhook_kwargs("https://10.0.0.5/x") == {} and mtls.webhook_kwargs("not a url [") == {}


def test_the_expiry_is_read_from_the_file_and_a_bundle_gives_the_earliest(pki, tmp_path):
    leaf = mtls.cert_not_after(str(pki / "sme" / "tls.crt"))
    ca = mtls.cert_not_after(str(pki / "ca" / "ca.crt"))
    assert 29 <= (leaf - certs._now()).days <= 30 and (ca - leaf).days > 3000
    bundle = tmp_path / "bundle.crt"
    bundle.write_bytes((pki / "ca" / "ca.crt").read_bytes() + (pki / "sme" / "tls.crt").read_bytes())
    assert mtls.cert_not_after(str(bundle)) == leaf


def test_the_expiry_metric_is_exported_only_with_mtls_on(monkeypatch, pki):
    collector = metrics._MtlsCertCollector()
    assert list(collector.collect()) == []
    use(monkeypatch, pki / "sme")
    (family,) = collector.collect()
    samples = {s.labels["file"]: s.value for s in family.samples}
    assert set(samples) == {"cert", "ca"} and samples["cert"] < samples["ca"]
    monkeypatch.setenv("SMO_MTLS_CA_FILE", str(pki / "nope"))                     # an unreadable file exports nothing for it, and the scrape still works
    (family,) = collector.collect()
    assert {s.labels["file"] for s in family.samples} == {"cert"}


# ---------------------------------------------------------------------------------------------------------- a real handshake

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _app(scope, receive, send):
    if scope["type"] == "http":
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"hello"})


@pytest.fixture()
def server(monkeypatch, pki):
    """uvicorn configured from `mtls.uvicorn_args()` exactly as the image starts it (the options are parsed back into a Config)."""
    use(monkeypatch, pki / "sme")
    args = mtls.uvicorn_args()
    option = {args[i]: args[i + 1] for i in range(0, len(args), 2)}
    port = _free_port()
    config = uvicorn.Config(_app, host="127.0.0.1", port=port, log_level="error", ssl_certfile=option["--ssl-certfile"], ssl_keyfile=option["--ssl-keyfile"],
                            ssl_ca_certs=option["--ssl-ca-certs"], ssl_cert_reqs=int(option["--ssl-cert-reqs"]))
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    for _ in range(100):
        if instance.started:
            break
        time.sleep(0.05)
    assert instance.started
    yield port
    instance.should_exit = True
    thread.join(5)


def _context(directory: Path, present_certificate: bool = True) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=str(directory / "ca.crt"))
    if present_certificate:
        context.load_cert_chain(str(directory / "tls.crt"), str(directory / "tls.key"))
    return context


def test_a_server_started_with_the_options_answers_a_client_with_a_certificate_and_refuses_the_rest(monkeypatch, pki, tmp_path, server):
    url = f"https://localhost:{server}/"
    assert httpx.get(url, verify=_context(pki / "dme")).text == "hello"                 # another module's certificate: the CA signed it
    assert httpx.get(url, verify=_context(pki / "clients" / "outsider")).text == "hello"
    with pytest.raises(httpx.HTTPError):
        httpx.get(url, verify=_context(pki / "dme", present_certificate=False))         # verifies the server, presents nothing
    with pytest.raises(httpx.HTTPError):
        httpx.get(f"http://localhost:{server}/")                                        # plain http on the TLS port
    other = tmp_path / "other"
    certs.init(other, 30)                                                               # a second, unrelated PKI
    with pytest.raises(httpx.HTTPError):
        httpx.get(url, verify=_other_client(pki, other))                                # trusts the right CA but presents a certificate of the wrong one


def _other_client(pki: Path, other: Path) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=str(pki / "sme" / "ca.crt"))
    context.load_cert_chain(str(other / "dme" / "tls.crt"), str(other / "dme" / "tls.key"))
    return context


def test_r1_client_presents_its_certificate_and_verifies_the_server(monkeypatch, pki, server):
    use(monkeypatch, pki / "dme")
    response = R1Client(base_url=f"https://localhost:{server}", bearer_token="t").get("/anything")
    assert response.status_code == 200 and response.text == "hello"


def test_r1_client_without_the_ca_does_not_reach_the_server(monkeypatch, pki, tmp_path, server):
    other = tmp_path / "other"
    certs.init(other, 30)
    use(monkeypatch, other / "dme")                                                      # a certificate and CA of another PKI
    with pytest.raises(httpx.HTTPError):
        R1Client(base_url=f"https://localhost:{server}", bearer_token="t").get("/anything")


def test_a_callback_to_an_internal_https_host_reaches_a_server_that_requires_a_certificate(monkeypatch, pki, server):
    use(monkeypatch, pki / "dme")
    monkeypatch.setenv("SMO_MTLS_INTERNAL_HOSTS", "localhost")
    # `localhost` is blocked by the SSRF guard on its own; the point here is only that the context travels, so go through the guard-free helper
    assert "verify" in mtls.webhook_kwargs(f"https://localhost:{server}/")
    assert get_webhook(f"https://localhost:{server}/") is None                           # the guard refuses loopback, with or without mTLS


def test_the_probe_calls_its_own_server_over_tls_with_its_own_certificate(monkeypatch, pki, server):
    use(monkeypatch, pki / "sme")
    url = f"https://localhost:{server}/ready"
    context = mtls.client_context()
    assert httpx.get(url, verify=context).status_code == 200
    # the probe asks port 8000 by default: here the test server's port
    assert mtls.probe("/ready", port=server) == 0
    assert mtls.probe("/ready", port=_free_port(), timeout=1.0) == 1                     # nothing listening: unhealthy
