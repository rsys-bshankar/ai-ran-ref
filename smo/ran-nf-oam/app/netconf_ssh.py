"""NETCONF over SSH (RFC 6242) — `o1_adaptor_endpoint.transport = "ssh"` (PR-SB-1, docs/adr/0002-netconf-over-ssh-client.md).

paramiko opens the SSH session and the `netconf` subsystem; this module adds the `<hello>` exchange and the two message
framings (base:1.0 end-of-message `]]>]]>`, base:1.1 chunked) and then reuses `netconf_client`'s RPC builders and reply
parsers, so the same `EditResult` reasons come out of both transports:

  - NETCONF_TIMEOUT      connect, hello or reply took longer than NETCONF_TIMEOUT_SECONDS (retryable)
  - NETCONF_UNREACHABLE  TCP/SSH could not be established (retryable)
  - NETCONF_RPC_FAILED   host key refused, authentication refused, no netconf subsystem, bad hello, or an `<rpc-error>`
                         (not retryable: repeating it cannot succeed)

The endpoint's `adaptor_uri` is `ssh://user@host[:port]` (default port 830), optionally with `?model=<name>` (a YANG model payload, yang_payload.py)
and `?datastore=candidate` (each write is one transaction on the candidate datastore, PR-SB-1.8: lock, edit-config, commit, unlock; a failed step
sends discard-changes first so nothing half-applied is left behind; default `running`). Host keys are checked against the keys an operator pinned for the endpoint (PR-SB-2.3) and
`NETCONF_SSH_KNOWN_HOSTS`, and an unknown or changed key is always refused; credentials are per endpoint (PR-SB-2): the endpoint's `credential_ref` names them, see `credentials_for`.
"""

import logging
import os
import base64
import hashlib
import re
import socket
import ssl
from urllib.parse import urlsplit

import paramiko
from smo_shared.secretfile import read_secret

from . import yang_payload
from .netconf_client import (NETCONF_BASE_NS, NETCONF_TIMEOUT_SECONDS, EditResult, build_edit_config_rpc,
                             build_get_config_rpc, config_attributes, edit_outcome, parse_reply)

log = logging.getLogger("ran-nf-oam.netconf-ssh")

DEFAULT_PORT = 830
DEFAULT_PORT_SSH = 22                   # paramiko names a known_hosts entry for any other port as "[host]:port"
EOM = b"]]>]]>"
BASE_10 = "urn:ietf:params:netconf:base:1.0"
BASE_11 = "urn:ietf:params:netconf:base:1.1"
MAX_MESSAGE = 16 * 1024 * 1024        # a reply larger than this is refused rather than buffered
CLIENT_HELLO = (f'<hello xmlns="{NETCONF_BASE_NS}"><capabilities><capability>{BASE_10}</capability>'
                f"<capability>{BASE_11}</capability></capabilities></hello>")


class NetconfSshError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail = reason, detail


CREDENTIAL_REF = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")


def check_credential_ref(ref: str) -> None:
    """A credential reference is a lower-case name, not a secret: ValueError (which never repeats the value) when it is not shaped like one,
    or names no credential this service has been given. Used when an endpoint is registered (PR-SB-2.1)."""
    if not CREDENTIAL_REF.match(ref):
        raise ValueError("credentialRef must be a credential name (lower-case letters, digits, '-' and '_', starting with a letter), "
                         "never the secret itself")
    if not credential_configured(ref):
        raise ValueError("credentialRef names no credential configured on this service: it is a name, never the secret itself "
                         "(see NETCONF_CRED_<NAME>_PASSWORD / _PASSWORD_FILE / _KEY_FILE in docs/SECRETS.md)")


def _cred_var(ref: str, part: str) -> str:
    return f"NETCONF_CRED_{ref.upper().replace('-', '_')}_{part}"


def credential_configured(ref: str) -> bool:
    return any(os.environ.get(_cred_var(ref, part)) or os.environ.get(_cred_var(ref, part) + "_FILE")
               for part in ("PASSWORD", "KEY_FILE", "CERT_FILE"))


def credentials_for(ref: str | None) -> tuple[str | None, str | None]:
    """(password, private key file) for an endpoint. With a `credential_ref` they are `NETCONF_CRED_<REF>_PASSWORD` (or `_PASSWORD_FILE`, the
    `*_FILE` convention of smo_shared/secretfile.py) and `NETCONF_CRED_<REF>_KEY_FILE`, from this service's own environment or mounted
    secrets: the database holds the name, never the value. A reference that resolves to nothing is an error (no fallback to the shared
    credential: an endpoint that names one must get that one). Without a reference the shared `NETCONF_SSH_PASSWORD` / `NETCONF_SSH_KEY_FILE`
    of PR-SB-1 apply."""
    if ref is None:
        return read_secret("NETCONF_SSH_PASSWORD"), os.environ.get("NETCONF_SSH_KEY_FILE") or None
    try:
        password = read_secret(_cred_var(ref, "PASSWORD"))
    except Exception as exc:                                    # noqa: BLE001 - a missing or conflicting secret file: say which variable, never a value
        raise NetconfSshError("NETCONF_RPC_FAILED", f"credential {ref!r}: {type(exc).__name__}") from exc
    key_file = os.environ.get(_cred_var(ref, "KEY_FILE")) or None
    if password is None and key_file is None:
        raise NetconfSshError("NETCONF_RPC_FAILED", f"credential {ref!r} is not configured on this service")
    return password, key_file


def parse_ssh_uri(adaptor_uri: str) -> tuple[str, str, int]:
    """(user, host, port) of an `ssh://user@host[:port]` URI; NetconfSshError when it is not one."""
    parts = urlsplit(adaptor_uri)
    try:
        port = DEFAULT_PORT if parts.port is None else parts.port
    except ValueError:
        port = 0
    if parts.scheme != "ssh" or not parts.hostname or not parts.username or not 0 < port < 65536:
        raise NetconfSshError("NETCONF_RPC_FAILED", "an ssh adaptor URI is ssh://user@host[:port][?model=name][&datastore=candidate]")
    try:
        yang_payload.model_of(adaptor_uri)
        yang_payload.datastore_of(adaptor_uri)
    except ValueError as exc:
        raise NetconfSshError("NETCONF_RPC_FAILED", str(exc)) from exc
    return parts.username, parts.hostname, port


def parse_host_key(key_type: str, public_key_b64: str) -> "paramiko.PKey":
    """The public key an operator pinned (`key_type` as in a known_hosts line, `ssh-ed25519`, `ecdsa-sha2-nistp256`, `ssh-rsa`...; the base64 body of
    that line). ValueError for anything paramiko cannot read as that type of key."""
    try:
        return paramiko.PKey.from_type_string(key_type, base64.b64decode(public_key_b64, validate=True))
    except Exception as exc:                                    # noqa: BLE001 - bad base64, wrong type, truncated blob: all the same answer
        raise ValueError(f"not a valid {key_type} public key") from exc


def host_key_fingerprint(key: "paramiko.PKey") -> str:
    """OpenSSH's `SHA256:<unpadded base64>` form, which is what an operator compares against `ssh-keygen -lf` or the device's own label."""
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def _host_key_policy(client: paramiko.SSHClient, host: str, port: int, pinned: list[tuple[str, str]] | None = None) -> None:
    """Keys to trust: those an operator pinned for this endpoint (`pinned`: (type, base64) pairs, PR-SB-2.3) and the lines of the file named by
    NETCONF_SSH_KNOWN_HOSTS. Neither: refuse. A key the server presents that is not among them, or differs from the one for its type, is refused:
    never trust on first use."""
    known_hosts = os.environ.get("NETCONF_SSH_KNOWN_HOSTS", "")
    if known_hosts:
        try:
            client.load_host_keys(known_hosts)
        except OSError as exc:
            raise NetconfSshError("NETCONF_RPC_FAILED", f"NETCONF_SSH_KNOWN_HOSTS cannot be read: {exc.strerror}") from exc
    for key_type, public_key in pinned or []:
        try:
            key = parse_host_key(key_type, public_key)
        except ValueError as exc:
            raise NetconfSshError("NETCONF_RPC_FAILED", f"a pinned host key for {host} is unreadable: {exc}") from exc
        client.get_host_keys().add(host if port == DEFAULT_PORT_SSH else f"[{host}]:{port}", key.get_name(), key)
    if known_hosts or pinned:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())     # never trust a key on first use
    else:
        raise NetconfSshError("NETCONF_RPC_FAILED", "no host keys known: pin one for the endpoint or set NETCONF_SSH_KNOWN_HOSTS")


class NetconfSession:
    """One SSH connection with an established NETCONF session: `with NetconfSession(uri) as s: s.rpc(xml)`."""

    def __init__(self, adaptor_uri: str, timeout: float | None = None, credential_ref: str | None = None,
                 host_keys: list[tuple[str, str]] | None = None):
        self.user, self.host, self.port = parse_ssh_uri(adaptor_uri)
        self.credential_ref, self.host_keys = credential_ref, host_keys
        self.timeout = NETCONF_TIMEOUT_SECONDS if timeout is None else timeout
        self.chunked = False
        self.server_capabilities: list[str] = []
        self._client: paramiko.SSHClient | None = None
        self._channel: paramiko.Channel | None = None
        self._buffer = b""

    def __enter__(self) -> "NetconfSession":
        try:
            self._connect()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        for resource in (self._channel, self._client):
            try:
                if resource is not None:
                    resource.close()
            except Exception:                                   # noqa: BLE001 - closing must never mask the real error
                pass
        self._channel = self._client = None

    def _connect(self) -> None:
        client = self._client = paramiko.SSHClient()
        _host_key_policy(client, self.host, self.port, self.host_keys)
        password, key_file = credentials_for(self.credential_ref)
        try:
            client.connect(self.host, port=self.port, username=self.user, password=password,
                           key_filename=key_file, timeout=self.timeout, banner_timeout=self.timeout,
                           auth_timeout=self.timeout, allow_agent=False, look_for_keys=False)
            channel = self._channel = client.get_transport().open_session(timeout=self.timeout)
            channel.settimeout(self.timeout)
            channel.invoke_subsystem("netconf")
        except paramiko.BadHostKeyException as exc:
            raise NetconfSshError("NETCONF_RPC_FAILED", f"host key for {self.host} does not match the known one") from exc
        except paramiko.SSHException as exc:
            if "not found in known_hosts" in str(exc):
                raise NetconfSshError("NETCONF_RPC_FAILED", f"host key for {self.host} is not in NETCONF_SSH_KNOWN_HOSTS") from exc
            if isinstance(exc, paramiko.AuthenticationException):
                raise NetconfSshError("NETCONF_RPC_FAILED", f"authentication refused for {self.user}@{self.host}") from exc
            if "timed out" in str(exc).lower():
                raise NetconfSshError("NETCONF_TIMEOUT", f"SSH negotiation with {self.host}:{self.port} timed out") from exc
            raise NetconfSshError("NETCONF_RPC_FAILED", f"SSH session to {self.host}:{self.port}: {exc}") from exc
        except TimeoutError as exc:
            raise NetconfSshError("NETCONF_TIMEOUT", f"connect to {self.host}:{self.port} timed out") from exc
        except OSError as exc:
            raise NetconfSshError("NETCONF_UNREACHABLE", f"{self.host}:{self.port}: {exc.strerror or type(exc).__name__}") from exc
        self._hello()

    def _hello(self) -> None:
        self._send_raw(CLIENT_HELLO.encode() + EOM)             # the hello is always end-of-message framed
        hello = parse_reply_any(self._read_eom(), "hello")
        self.server_capabilities = [c.text.strip() for c in hello.iter() if c.tag.rsplit("}", 1)[-1] == "capability" and c.text]
        if BASE_10 not in self.server_capabilities and BASE_11 not in self.server_capabilities:
            raise NetconfSshError("NETCONF_RPC_FAILED", "the server's hello offers neither base:1.0 nor base:1.1")
        self.chunked = BASE_11 in self.server_capabilities      # both sides offered 1.1: chunked framing from here on

    # --- framing -------------------------------------------------------------------------------------------------
    def _send_raw(self, data: bytes) -> None:
        try:
            self._channel.sendall(data)
        except TimeoutError as exc:
            raise NetconfSshError("NETCONF_TIMEOUT", "send timed out") from exc
        except ssl.SSLError as exc:                      # TLS only: the peer refused the session (with TLS 1.3 this is how a rejected client certificate shows)
            raise NetconfSshError("NETCONF_RPC_FAILED", f"the TLS session was refused: {exc.reason or type(exc).__name__}") from exc
        except OSError as exc:
            raise NetconfSshError("NETCONF_UNREACHABLE", f"send failed: {exc}") from exc

    def _more(self) -> None:
        try:
            data = self._channel.recv(65536)
        except (TimeoutError, socket.timeout) as exc:
            raise NetconfSshError("NETCONF_TIMEOUT", "no reply within the timeout") from exc
        except ssl.SSLError as exc:
            raise NetconfSshError("NETCONF_RPC_FAILED", f"the TLS session was refused: {exc.reason or type(exc).__name__}") from exc
        except OSError as exc:
            raise NetconfSshError("NETCONF_UNREACHABLE", f"receive failed: {exc}") from exc
        if not data:
            raise NetconfSshError("NETCONF_UNREACHABLE", "the server closed the session")
        self._buffer += data
        if len(self._buffer) > MAX_MESSAGE:
            raise NetconfSshError("NETCONF_RPC_FAILED", "reply larger than the allowed maximum")

    def _read_eom(self) -> str:
        while EOM not in self._buffer:
            self._more()
        message, _, self._buffer = self._buffer.partition(EOM)
        return message.decode("utf-8", "replace")

    def _read_chunked(self) -> str:
        message = b""
        while True:
            while not (match := re.match(rb"\n#(?:(\d+)|#)\n", self._buffer)):
                if len(self._buffer) > 16 and not self._buffer.startswith(b"\n#"):
                    raise NetconfSshError("NETCONF_RPC_FAILED", "malformed chunk header")
                self._more()
            self._buffer = self._buffer[match.end():]
            if match.group(1) is None:                            # "\n##\n": end of message
                return message.decode("utf-8", "replace")
            size = int(match.group(1))
            if size == 0 or size > 4294967295:
                raise NetconfSshError("NETCONF_RPC_FAILED", "chunk size out of range")
            while len(self._buffer) < size:
                self._more()
            message += self._buffer[:size]
            self._buffer = self._buffer[size:]
            if len(message) > MAX_MESSAGE:
                raise NetconfSshError("NETCONF_RPC_FAILED", "reply larger than the allowed maximum")

    def rpc(self, xml: str):
        """Send one RPC and return the `<rpc-reply>` element (NetconfSshError for a transport failure)."""
        payload = xml.encode()
        self._send_raw(b"\n#%d\n%s\n##\n" % (len(payload), payload) if self.chunked else payload + EOM)
        return parse_reply_any(self._read_chunked() if self.chunked else self._read_eom(), "rpc-reply")


def parse_reply_any(text: str, expected: str):
    root = parse_reply(text) if expected == "rpc-reply" else _parse_named(text, expected)
    if root is None:
        raise NetconfSshError("NETCONF_RPC_FAILED", f"the server did not answer with <{expected}>")
    return root


def _parse_named(text: str, name: str):
    import defusedxml.ElementTree as ET
    from defusedxml.common import DefusedXmlException
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, DefusedXmlException):
        return None
    return root if root.tag.rsplit("}", 1)[-1] == name else None


CANDIDATE = "urn:ietf:params:netconf:capability:candidate:1.0"


def _step_rpc(message_id: str, name: str, body: str) -> str:
    return f'<rpc message-id="{message_id}-{name}" xmlns="{NETCONF_BASE_NS}">{body}</rpc>'


def candidate_transaction(session: "NetconfSession", message_id: str, edit_rpc: str) -> EditResult:
    """RFC 6241 sections 8.3 and 7.5-7.8 as one unit: lock the candidate, edit it, commit, unlock. A refused lock changes nothing. A refused
    edit or commit is followed by discard-changes, so the candidate is left as it was found; the unlock always follows (best effort: a
    failed unlock after a good commit does not undo the commit). The result is the first failure's, with the step named in the detail."""
    if CANDIDATE not in session.server_capabilities:
        return EditResult(False, "NETCONF_RPC_FAILED", "the server does not offer the candidate datastore (:candidate:1.0)")

    def step(name: str, body: str) -> EditResult:
        outcome = edit_outcome(session.rpc(_step_rpc(message_id, name, body)))
        return outcome if outcome else EditResult(False, outcome.reason, f"{name}: {outcome.detail or 'refused'}")

    locked = step("lock", "<lock><target><candidate/></target></lock>")
    if not locked:
        return locked                                  # nothing was touched: no discard, no unlock
    try:
        result = edit_outcome(session.rpc(edit_rpc))
        if not result:
            result = EditResult(False, result.reason, f"edit-config: {result.detail or 'refused'}")
        else:
            result = step("commit", "<commit/>")
        if not result:
            try:
                step("discard", "<discard-changes/>")
            except NetconfSshError as exc:
                log.warning("discard-changes after a failed write: %s", exc)
        return result
    finally:
        try:
            step("unlock", "<unlock><target><candidate/></target></unlock>")
        except NetconfSshError as exc:
            log.warning("unlock of the candidate: %s", exc)


def open_session(adaptor_uri: str, credential_ref: str | None = None, host_keys: list[tuple[str, str]] | None = None) -> "NetconfSession":
    """The session for an endpoint's URI: NETCONF over TLS (netconf_tls.py, PR-SB-2.4) for `tls://`, over SSH otherwise."""
    if adaptor_uri.lower().startswith("tls:"):
        from .netconf_tls import NetconfTlsSession                  # imported here: that module builds on this one
        return NetconfTlsSession(adaptor_uri, credential_ref=credential_ref)
    return NetconfSession(adaptor_uri, credential_ref=credential_ref, host_keys=host_keys)


# Same call shapes as netconf_client.send_edit_config / send_get_config, which is how main.py picks them.
def send_edit_config(adaptor_uri: str, target_ref: str, attribute_changes: dict, message_id: str, operation: str = "merge",
                     managed_function_ref: str | None = None, credential_ref: str | None = None,
                     host_keys: list[tuple[str, str]] | None = None) -> EditResult:
    model = yang_payload.model_of(adaptor_uri)
    datastore = yang_payload.datastore_of(adaptor_uri)
    try:
        rpc = (yang_payload.build_edit_config_rpc(yang_payload.PROFILES[model], message_id, target_ref, attribute_changes, operation,
                                                  managed_function_ref, datastore)
               if model else build_edit_config_rpc(message_id, target_ref, attribute_changes, operation, managed_function_ref, datastore))
    except ValueError as exc:                                      # an operation the model path does not know: nothing is sent
        return EditResult(False, "NETCONF_RPC_FAILED", str(exc))
    try:
        with open_session(adaptor_uri, credential_ref, host_keys) as session:
            if datastore == "candidate":
                return candidate_transaction(session, message_id, rpc)
            return edit_outcome(session.rpc(rpc))
    except NetconfSshError as exc:
        log.warning("edit-config on %s failed: %s", target_ref, exc)
        return EditResult(False, exc.reason, exc.detail or None)


def send_get_config(adaptor_uri: str, target_ref: str, message_id: str, managed_function_ref: str | None = None,
                    credential_ref: str | None = None, host_keys: list[tuple[str, str]] | None = None) -> dict | None:
    model = yang_payload.model_of(adaptor_uri)
    profile = yang_payload.PROFILES[model] if model else None
    try:
        with open_session(adaptor_uri, credential_ref, host_keys) as session:
            if profile:
                reply = session.rpc(yang_payload.build_get_config_rpc(profile, message_id, target_ref, managed_function_ref))
                return yang_payload.config_attributes(profile, reply, target_ref, managed_function_ref)
            return config_attributes(session.rpc(build_get_config_rpc(message_id, target_ref, managed_function_ref)))
    except NetconfSshError as exc:
        log.warning("get-config on %s failed: %s", target_ref, exc)
        return None
