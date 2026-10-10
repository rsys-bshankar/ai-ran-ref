"""smo_shared/mtls.py (PR-SEC-2): off means nothing changes; on means a certificate is needed, fail closed, and a real handshake proves the refusal.

Run with: cd smo/shared && PYTHONPATH=. python -m pytest tests/test_mtls.py -q
"""

import importlib.util
import os
import socket
import ssl
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from smo_shared import metrics, mtls
from smo_shared.r1_client import R1Client
from smo_shared.webhook import get_webhook

SCRIPT = next(p / "scripts" / "mtls_certs.py" for p in Path(__file__).resolve().parents if (p / "scripts" / "mtls_certs.py").exists())   # also from the mutation pilot's copy of this directory


class _Certs:
    """The certificate script, loaded on first use: importing `cryptography` while collecting would put it in the parent of every forked mutant of
    scripts/mutation_pilot.sh, where its tests then segfault (found when this file was added)."""
    _module = None

    def __getattr__(self, name):
        if _Certs._module is None:
            spec = importlib.util.spec_from_file_location("mtls_certs", SCRIPT)
            _Certs._module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(_Certs._module)
        return getattr(_Certs._module, name)


certs = _Certs()

ENV = ("SMO_MTLS", "SMO_MTLS_SERVE", "SMO_MTLS_CERT_FILE", "SMO_MTLS_KEY_FILE", "SMO_MTLS_CA_FILE", "SMO_MTLS_INTERNAL_HOSTS")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, request):
    """Autouse: removes every SMO_MTLS* variable so each test starts with mTLS off; skips the tests that need certificates or a TLS server when run
    under the mutation pilot.
    """
    if "mutmut" in sys.modules and {"pki", "server"} & set(request.fixturenames):
        # scripts/mutation_pilot.sh runs this suite in a process that then forks one child per mutant; loading `cryptography` (the certificates) or starting a TLS
        # server thread before that fork crashes the children (segfault, found when this file was added). The mutants of ratelimit.py and roles.py are not about
        # this module; the tests below that need no certificate still run there, and every test runs in the ordinary suite.
        pytest.skip("not under the mutation pilot")
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    """A throw-away CA and per-module certificates (`sme`, `dme`, `gui-bff`, ...) plus an `outsider` client certificate, made once per test file by
    scripts/mtls_certs.py; the tests copy or point at them.
    """
    root = tmp_path_factory.mktemp("pki")
    certs.init(root, 30)
    certs.new_client(root, "outsider", 30)
    return root


def use(monkeypatch, directory: Path) -> None:
    """Helper: turns mTLS on and points the cert, key and CA settings at the files in `directory`."""
    monkeypatch.setenv("SMO_MTLS", "on")
    monkeypatch.setenv("SMO_MTLS_CERT_FILE", str(directory / "tls.crt"))
    monkeypatch.setenv("SMO_MTLS_KEY_FILE", str(directory / "tls.key"))
    monkeypatch.setenv("SMO_MTLS_CA_FILE", str(directory / "ca.crt"))


def test_off_by_default_nothing_changes():
    """With SMO_MTLS unset nothing changes: URLs stay http, no client or server TLS arguments, and the command line prints no options."""
    assert not mtls.enabled() and not mtls.serving()
    assert mtls.http_url("http://sme:8000") == "http://sme:8000"
    assert mtls.client_kwargs("https://sme:8000") == {} and mtls.client_kwargs() == {}
    assert mtls.webhook_kwargs("https://ran-nf-oam:8000/cb") == {}
    assert mtls.uvicorn_args() == []
    assert mtls.main(["uvicorn-args"]) == 0


# Table: values of SMO_MTLS that must not turn mTLS on (off, empty, 0, false, no, and an unrecognised word).
@pytest.mark.parametrize("value", ["off", "", "0", "false", "no", "maybe"])
def test_only_an_explicit_on_turns_it_on(monkeypatch, value):
    monkeypatch.setenv("SMO_MTLS", value)
    assert not mtls.enabled()


def test_on_upgrades_internal_addresses_and_nothing_else(monkeypatch, pki):
    """With mTLS on, an http:// address becomes https://; https and other schemes are untouched."""
    use(monkeypatch, pki / "sme")
    assert mtls.http_url("http://sme:8000") == "https://sme:8000"
    assert mtls.http_url("https://already:8000") == "https://already:8000"
    assert mtls.http_url("ftp://x") == "ftp://x"


def test_server_options_require_a_client_certificate_and_name_the_files(monkeypatch, pki):
    """The uvicorn options name this module's certificate, key and CA files and require a client certificate (CERT_REQUIRED)."""
    use(monkeypatch, pki / "sme")
    args = mtls.uvicorn_args()
    assert args[args.index("--ssl-cert-reqs") + 1] == str(int(ssl.CERT_REQUIRED)) == "2"
    assert args[args.index("--ssl-certfile") + 1].endswith("sme/tls.crt")
    assert args[args.index("--ssl-keyfile") + 1].endswith("sme/tls.key")
    assert args[args.index("--ssl-ca-certs") + 1].endswith("sme/ca.crt")


def test_a_client_only_process_serves_plain_http(monkeypatch, pki):
    """With SMO_MTLS_SERVE=off (the GUI backend) the process serves plain HTTP but still presents its certificate on outbound calls."""
    use(monkeypatch, pki / "gui-bff")
    monkeypatch.setenv("SMO_MTLS_SERVE", "off")
    assert mtls.enabled() and not mtls.serving() and mtls.uvicorn_args() == []
    assert "verify" in mtls.client_kwargs("https://r1-termination:8000")


def test_fail_closed_when_a_file_is_missing_or_empty(monkeypatch, tmp_path, capsys):
    """A missing or empty certificate file raises MtlsError for the server options and for client calls (never an unverified call), and the command
    line exits 1 naming the file.
    """
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
    """A private key that does not belong to the certificate fails with 'do not load' instead of starting half-configured."""
    use(monkeypatch, pki / "sme")
    monkeypatch.setenv("SMO_MTLS_KEY_FILE", str(pki / "dme" / "tls.key"))
    with pytest.raises(mtls.MtlsError, match="do not load"):
        mtls.client_context()


def test_the_client_context_verifies_and_is_rebuilt_when_the_files_change(monkeypatch, pki, tmp_path):
    """The client context verifies servers and hostnames, is cached while the files are unchanged, and is rebuilt after a certificate renewal changes
    them.
    """
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


# Table: (host, is it inside the deployment). Service names, *.svc and *.svc.cluster.local are; public names, IP addresses, an empty host and None are
# not.
@pytest.mark.parametrize("host,internal", [
    ("ran-nf-oam", True), ("r1-termination", True), ("sme.smo.svc", True), ("sme.smo.svc.cluster.local", True), ("SME", True),
    ("example.com", False), ("rapp.example.org", False), ("10.1.2.3", False), ("::1", False), ("", False), (None, False),
])
def test_which_callback_hosts_are_inside_the_deployment(host, internal):
    assert mtls.is_internal_host(host) is internal


def test_extra_internal_host_patterns(monkeypatch):
    """SMO_MTLS_INTERNAL_HOSTS adds fnmatch patterns of hosts that count as inside the deployment."""
    assert not mtls.is_internal_host("rapp.corp.example")
    monkeypatch.setenv("SMO_MTLS_INTERNAL_HOSTS", "*.corp.example, other.internal")
    assert mtls.is_internal_host("rapp.corp.example") and mtls.is_internal_host("other.internal")
    assert not mtls.is_internal_host("evil.example")


def test_a_callback_carries_the_certificate_only_to_an_https_internal_destination(monkeypatch, pki):
    """A callback gets the client certificate only when it is https and its host is internal; plain, public, IP and malformed destinations get none.
    """
    use(monkeypatch, pki / "sme")
    assert "verify" in mtls.webhook_kwargs("https://ran-nf-oam:8000/dme-jobs")
    assert mtls.webhook_kwargs("http://ran-nf-oam:8000/dme-jobs") == {}          # plain stays plain
    assert mtls.webhook_kwargs("https://hooks.example.com/x") == {}              # a stranger gets the system roots and no certificate
    assert mtls.webhook_kwargs("https://10.0.0.5/x") == {} and mtls.webhook_kwargs("not a url [") == {}


def test_the_expiry_is_read_from_the_file_and_a_bundle_gives_the_earliest(pki, tmp_path):
    """cert_not_after reads the expiry from the file and, for a bundle, returns the earliest of its certificates."""
    leaf = mtls.cert_not_after(str(pki / "sme" / "tls.crt"))
    ca = mtls.cert_not_after(str(pki / "ca" / "ca.crt"))
    assert 29 <= (leaf - certs._now()).days <= 30 and (ca - leaf).days > 3000
    bundle = tmp_path / "bundle.crt"
    bundle.write_bytes((pki / "ca" / "ca.crt").read_bytes() + (pki / "sme" / "tls.crt").read_bytes())
    assert mtls.cert_not_after(str(bundle)) == leaf


def test_the_expiry_metric_is_exported_only_with_mtls_on(monkeypatch, pki):
    """The certificate-expiry gauge is exported only with mTLS on, and an unreadable file is left out without failing the scrape."""
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
    """Helper: a TCP port on localhost that is free at the moment of the call."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _app(scope, receive, send):
    if scope["type"] == "http":
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"hello"})


@pytest.fixture()
def server(monkeypatch, pki):
    """A real uvicorn server on a free port, configured from mtls.uvicorn_args() exactly as the image starts it, running in a thread; yields the port
    and stops it afterwards.
    """
    import uvicorn
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
    """Helper: a client SSL context trusting the CA in `directory`, presenting that directory's certificate unless told not to."""
    context = ssl.create_default_context(cafile=str(directory / "ca.crt"))
    if present_certificate:
        context.load_cert_chain(str(directory / "tls.crt"), str(directory / "tls.key"))
    return context


def test_a_server_started_with_the_options_answers_a_client_with_a_certificate_and_refuses_the_rest(monkeypatch, pki, tmp_path, server):
    """A real handshake: clients with a certificate signed by the CA are answered; a client with none, plain http, or a certificate of another CA are
    refused.
    """
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
    """Helper: a client context that trusts the right CA but presents a certificate from a different PKI."""
    context = ssl.create_default_context(cafile=str(pki / "sme" / "ca.crt"))
    context.load_cert_chain(str(other / "dme" / "tls.crt"), str(other / "dme" / "tls.key"))
    return context


def test_r1_client_presents_its_certificate_and_verifies_the_server(monkeypatch, pki, server):
    """R1Client with mTLS on reaches an mTLS server, presenting its certificate and verifying the server."""
    use(monkeypatch, pki / "dme")
    response = R1Client(base_url=f"https://localhost:{server}", bearer_token="t").get("/anything")
    assert response.status_code == 200 and response.text == "hello"


def test_r1_client_without_the_ca_does_not_reach_the_server(monkeypatch, pki, tmp_path, server):
    """R1Client whose certificate and CA belong to another PKI cannot reach the server."""
    other = tmp_path / "other"
    certs.init(other, 30)
    use(monkeypatch, other / "dme")                                                      # a certificate and CA of another PKI
    with pytest.raises(httpx.HTTPError):
        R1Client(base_url=f"https://localhost:{server}", bearer_token="t").get("/anything")


def test_a_callback_to_an_internal_https_host_reaches_a_server_that_requires_a_certificate(monkeypatch, pki, server):
    """A callback to an internal https host gets the mTLS context; the SSRF guard still refuses loopback, so the call itself returns None."""
    use(monkeypatch, pki / "dme")
    monkeypatch.setenv("SMO_MTLS_INTERNAL_HOSTS", "localhost")
    # `localhost` is blocked by the SSRF guard on its own; the point here is only that the context travels, so go through the guard-free helper
    assert "verify" in mtls.webhook_kwargs(f"https://localhost:{server}/")
    assert get_webhook(f"https://localhost:{server}/") is None                           # the guard refuses loopback, with or without mTLS


def test_the_probe_calls_its_own_server_over_tls_with_its_own_certificate(monkeypatch, pki, server):
    """The container health probe succeeds against an mTLS server using this module's own certificate, and reports unhealthy (1) when nothing listens.
    """
    use(monkeypatch, pki / "sme")
    url = f"https://localhost:{server}/ready"
    context = mtls.client_context()
    assert httpx.get(url, verify=context).status_code == 200
    # the probe asks port 8000 by default: here the test server's port
    assert mtls.probe("/ready", port=server) == 0
    assert mtls.probe("/ready", port=_free_port(), timeout=1.0) == 1                     # nothing listening: unhealthy
