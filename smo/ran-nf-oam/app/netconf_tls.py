"""NETCONF over TLS (RFC 7589) — `o1_adaptor_endpoint.transport = "tls"` (PR-SB-2.4).

The same NETCONF session as over SSH (`netconf_ssh.NetconfSession`: the `<hello>` exchange, both framings, the same `EditResult` reasons), carried by a
mutually authenticated TLS connection instead of an SSH channel. The endpoint's `adaptor_uri` is `tls://host[:port]` (default port 6513), with the same
`?model=` and `?datastore=` options as an ssh one.

  - The server is verified: its certificate must chain to the CA file and name the host (TLS 1.2 or later; there is no switch to skip either check).
  - The client presents a certificate: with the endpoint's `credential_ref`, `NETCONF_CRED_<NAME>_CERT_FILE`, `_KEY_FILE` and `_CA_FILE`; without one
    the shared `NETCONF_TLS_CERT_FILE`, `NETCONF_TLS_KEY_FILE` and `NETCONF_TLS_CA_FILE`. The files are read at every connect, so a mounted secret can
    be rotated without a restart. The private key is a file the service reads, never a value in the database.
  - The NETCONF user is the one the server derives from the client certificate (RFC 7589 section 7), so there is no user name in the URI.
"""

import os
import socket
import ssl
from urllib.parse import urlsplit

from . import yang_payload
from .netconf_ssh import NetconfSession, NetconfSshError, _cred_var

DEFAULT_TLS_PORT = 6513


def parse_tls_uri(adaptor_uri: str) -> tuple[str, int]:
    """(host, port) of a `tls://host[:port][?model=...&datastore=...]` URI; NetconfSshError when it is not one."""
    parts = urlsplit(adaptor_uri)
    try:
        port = DEFAULT_TLS_PORT if parts.port is None else parts.port
    except ValueError:
        port = 0
    if parts.scheme != "tls" or not parts.hostname or parts.username or not 0 < port < 65536:
        raise NetconfSshError("NETCONF_RPC_FAILED", "a tls adaptor URI is tls://host[:port][?model=name][&datastore=candidate]")
    try:
        yang_payload.model_of(adaptor_uri)
        yang_payload.datastore_of(adaptor_uri)
    except ValueError as exc:
        raise NetconfSshError("NETCONF_RPC_FAILED", str(exc)) from exc
    return parts.hostname, port


def tls_material_for(ref: str | None) -> tuple[str, str, str]:
    """(client certificate file, its private key file, CA file) for an endpoint: all three are required, with no fallback from a named credential
    to the shared one (an endpoint that names a credential must get that one)."""
    if ref is None:
        names = {part: f"NETCONF_TLS_{part}" for part in ("CERT_FILE", "KEY_FILE", "CA_FILE")}
    else:
        names = {part: _cred_var(ref, part) for part in ("CERT_FILE", "KEY_FILE", "CA_FILE")}
    files = {part: os.environ.get(var) or "" for part, var in names.items()}
    missing = [var for part, var in names.items() if not files[part]]
    if missing:
        raise NetconfSshError("NETCONF_RPC_FAILED", "TLS credential incomplete: set " + ", ".join(missing))
    return files["CERT_FILE"], files["KEY_FILE"], files["CA_FILE"]


class NetconfTlsSession(NetconfSession):
    """One TLS connection with an established NETCONF session: `with NetconfTlsSession(uri, credential_ref=...) as s: s.rpc(xml)`."""

    def __init__(self, adaptor_uri: str, timeout: float | None = None, credential_ref: str | None = None, host_keys=None):
        super().__init__("ssh://tls@placeholder", timeout, credential_ref, None)       # the base class's fields; the address below is the real one
        self.host, self.port = parse_tls_uri(adaptor_uri)

    def _connect(self) -> None:
        cert, key, ca = tls_material_for(self.credential_ref)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        try:
            context.load_verify_locations(cafile=ca)
            context.load_cert_chain(certfile=cert, keyfile=key)
        except (OSError, ssl.SSLError) as exc:                                         # a missing or malformed file: name the kind, never the contents
            raise NetconfSshError("NETCONF_RPC_FAILED", f"TLS credential files cannot be loaded: {type(exc).__name__}") from exc
        try:
            raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
            try:
                self._channel = context.wrap_socket(raw, server_hostname=self.host)
            except BaseException:
                raw.close()
                raise
            self._channel.settimeout(self.timeout)
        except ssl.SSLCertVerificationError as exc:
            raise NetconfSshError("NETCONF_RPC_FAILED", f"the certificate of {self.host}:{self.port} is not trusted or does not name the host: "
                                  f"{exc.verify_message}") from exc
        except TimeoutError as exc:
            raise NetconfSshError("NETCONF_TIMEOUT", f"TLS connect to {self.host}:{self.port} timed out") from exc
        except ssl.SSLError as exc:                                                     # handshake refused: for example the server rejected our certificate
            if "timed out" in str(exc).lower():
                raise NetconfSshError("NETCONF_TIMEOUT", f"TLS handshake with {self.host}:{self.port} timed out") from exc
            raise NetconfSshError("NETCONF_RPC_FAILED", f"TLS handshake with {self.host}:{self.port} failed: {exc.reason or type(exc).__name__}") from exc
        except OSError as exc:
            raise NetconfSshError("NETCONF_UNREACHABLE", f"{self.host}:{self.port}: {exc.strerror or type(exc).__name__}") from exc
        self._hello()
