"""Mutual TLS between services (PR-SEC-2), opt-in and off by default.

With `SMO_MTLS` unset (or `off`) nothing in this module changes behaviour: `http_url` returns its argument, `client_kwargs` is empty, the
container starts uvicorn as before. With `SMO_MTLS=on` the process

  * serves HTTPS and REFUSES a client that does not present a certificate signed by the CA (`uvicorn_args`: certfile, keyfile, ca-certs and
    `ssl_cert_reqs=CERT_REQUIRED`), unless `SMO_MTLS_SERVE=off` (the GUI backend: its caller is the GUI's nginx, which is not a mesh member);
  * presents its own certificate and verifies the server against the CA on every internal call (`client_kwargs`, for `httpx`), and upgrades the
    `http://` default addresses of its peers to `https://` (`http_url`).

Files (`SMO_MTLS_CERT_FILE`, `SMO_MTLS_KEY_FILE`, `SMO_MTLS_CA_FILE`; default `/run/mtls/tls.crt`, `tls.key`, `ca.crt`): one certificate per module,
used both as its server certificate and as its client certificate (extended key usage serverAuth and clientAuth), signed by one CA. `scripts/mtls_certs.py`
makes them for compose; the chart takes cert-manager Certificates or one Secret per module (`deploy/helm/smo/README.md`).

Fail closed: with `SMO_MTLS=on` and a file missing or unreadable the process does not start (`uvicorn_args` raises, and the Dockerfile's command exits on it
rather than starting without TLS), and a client call without a context is never made unverified.

A client reads its certificate files again when their modification time changes, so a renewed certificate is used by the next call without a restart. A
server loads its files once, at start: renewal reaches it by a (rolling) restart (`docs/ARCHITECTURE.md`, mutual TLS between services).

Callbacks: a caller-registered destination (`smo_shared.webhook`) gets the client certificate and the CA only when it is `https://` and names a host inside
the deployment (`is_internal_host`: a single-label name such as `ran-nf-oam`, `*.svc`, `*.svc.cluster.local`, or a pattern of `SMO_MTLS_INTERNAL_HOSTS`);
every other destination is called as before, so the private key's certificate is never offered to a stranger and a public destination is verified against
the system roots.

Command line (used by the image and by the orchestrators' probes):

    python -m smo_shared.mtls uvicorn-args     # the uvicorn options, or nothing when serving plain HTTP; exit 1 when the files are not there
    python -m smo_shared.mtls probe [PATH [PORT]]   # GET https://localhost:PORT PATH (default /ready, 8000) with this module's certificate; exit 0 on 2xx
"""

import fnmatch
import os
import ssl
import sys
import threading
from datetime import datetime, timezone
from urllib.parse import urlsplit

DEFAULT_DIR = "/run/mtls"
_ON = frozenset({"on", "1", "true", "yes", "require"})

_lock = threading.Lock()
_contexts: dict[tuple, ssl.SSLContext] = {}


class MtlsError(RuntimeError):
    """mTLS is on and what it needs is not usable."""


def enabled() -> bool:
    return os.environ.get("SMO_MTLS", "off").strip().lower() in _ON


def serving() -> bool:
    """This process terminates mTLS itself: on, and not switched off for the server side."""
    return enabled() and os.environ.get("SMO_MTLS_SERVE", "on").strip().lower() in _ON


def files() -> tuple[str, str, str]:
    """(certificate, key, CA) paths."""
    return (os.environ.get("SMO_MTLS_CERT_FILE") or f"{DEFAULT_DIR}/tls.crt",
            os.environ.get("SMO_MTLS_KEY_FILE") or f"{DEFAULT_DIR}/tls.key",
            os.environ.get("SMO_MTLS_CA_FILE") or f"{DEFAULT_DIR}/ca.crt")


def _require_readable() -> tuple[str, str, str]:
    """Returns the (certificate, key, CA) paths after checking each can be opened and is not empty; raises MtlsError naming the file otherwise.

    Called by every function that needs the files, so a missing file fails closed with a readable message instead of surfacing later as an SSL
    error.
    """
    paths = files()
    for path in paths:
        try:
            with open(path, "rb") as handle:
                if not handle.read(1):
                    raise MtlsError(f"SMO_MTLS is on but {path} is empty")
        except OSError as exc:
            raise MtlsError(f"SMO_MTLS is on but {path} cannot be read: {exc.strerror or exc}") from exc
    return paths


def http_url(url: str) -> str:
    """`http://x` -> `https://x` when mTLS is on; anything else unchanged."""
    if enabled() and url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


def is_internal_host(host: str | None) -> bool:
    """A name inside the deployment: no dot (a Compose or Kubernetes service name), `*.svc`, `*.svc.cluster.local`, or one of `SMO_MTLS_INTERNAL_HOSTS`
    (comma separated fnmatch patterns). An IP address is not internal by this rule: certificates here name services, not addresses."""
    if not host:
        return False
    host = host.lower().rstrip(".")
    if host.replace(".", "").isdigit() or ":" in host:
        return False
    if "." not in host or host.endswith(".svc") or host.endswith(".svc.cluster.local"):
        return True
    patterns = [p.strip().lower() for p in os.environ.get("SMO_MTLS_INTERNAL_HOSTS", "").split(",") if p.strip()]
    return any(fnmatch.fnmatchcase(host, p) for p in patterns)


def client_context() -> ssl.SSLContext:
    """The verifying client context with this module's certificate loaded; rebuilt when a file's modification time changes."""
    paths = _require_readable()
    key = tuple((p, os.stat(p).st_mtime_ns) for p in paths)
    with _lock:
        context = _contexts.get(key)
        if context is None:
            cert, private_key, ca = paths
            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            try:
                context.load_cert_chain(cert, private_key)
            except (ssl.SSLError, OSError) as exc:
                raise MtlsError(f"SMO_MTLS is on but the certificate and key do not load: {exc}") from exc
            # Only one context is ever kept: the key holds the files' modification times, so an older entry belongs to files that have since been
            # replaced.
            _contexts.clear()                      # one live context: an older one belongs to files that have since changed
            _contexts[key] = context
        return context


def client_kwargs(url: str | None = None) -> dict:
    """Keyword arguments for an `httpx` call to a peer: `{}` when mTLS is off or the URL is plain `http://` (an httpx call to an https URL needs `verify`).
    With `url=None` the context is returned for any peer."""
    if not enabled():
        return {}
    if url is not None and not url.startswith("https://"):
        return {}
    return {"verify": client_context()}


def webhook_kwargs(destination: str | None) -> dict:
    """What a callback to `destination` carries: the mTLS context only for an https destination inside the deployment (see the module docstring)."""
    if not enabled() or not destination:
        return {}
    try:
        parts = urlsplit(destination)
    except ValueError:
        return {}
    if parts.scheme != "https" or not is_internal_host(parts.hostname):
        return {}
    return {"verify": client_context()}


def uvicorn_args() -> list[str]:
    """The uvicorn options that make the server require a client certificate; `[]` when this process serves plain HTTP."""
    if not serving():
        return []
    cert, key, ca = _require_readable()
    return ["--ssl-certfile", cert, "--ssl-keyfile", key, "--ssl-ca-certs", ca, "--ssl-cert-reqs", str(int(ssl.CERT_REQUIRED))]


def server_context() -> ssl.SSLContext:
    """What uvicorn builds from `uvicorn_args`, for tests and for anything that serves TLS itself."""
    cert, key, ca = _require_readable()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    context.load_verify_locations(cafile=ca)
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def cert_not_after(path: str) -> datetime:
    """The earliest `notAfter` among the certificates in a PEM file (UTC): a leaf file has one, a CA bundle during a CA rotation has two."""
    from cryptography import x509
    with open(path, "rb") as handle:
        certificates = x509.load_pem_x509_certificates(handle.read())
    return min(c.not_valid_after_utc for c in certificates).astimezone(timezone.utc)


def probe(path: str = "/ready", port: int = 8000, timeout: float = 4.0) -> int:
    """The health check an orchestrator runs inside the container (the server refuses a client with no certificate, so a plain HTTP probe cannot ask):
    GET `path` on this process over TLS, presenting its own certificate. 0 when the answer is 2xx."""
    import urllib.request
    try:
        context = client_context() if serving() else None
        url = f"{'https' if context else 'http'}://localhost:{port}{path}"
        with urllib.request.urlopen(url, timeout=timeout, context=context) as response:   # noqa: S310 (a fixed local URL)
            return 0 if 200 <= response.status < 300 else 1
    except Exception as exc:   # an unhealthy answer, a refused connection and a bad certificate all mean "not healthy"
        print(f"probe failed: {exc!r}", file=sys.stderr)
        return 1


def main(argv: list[str]) -> int:
    """Command line of `python -m smo_shared.mtls`; returns the exit code.

    `uvicorn-args` prints the uvicorn options (nothing when serving plain HTTP) and returns 1 with the reason on stderr when mTLS is on and a file
    is unusable, which is how the image's start command refuses to start without TLS. `probe [PATH [PORT]]` returns the result of `probe`. Anything
    else prints the usage text from the module docstring and returns 2.
    """
    command = argv[0] if argv else ""
    if command == "uvicorn-args":
        try:
            print(" ".join(uvicorn_args()))
        except MtlsError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    if command == "probe":
        return probe(argv[1] if len(argv) > 1 else "/ready", int(argv[2]) if len(argv) > 2 else 8000)
    print(__doc__.split("Command line", 1)[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
