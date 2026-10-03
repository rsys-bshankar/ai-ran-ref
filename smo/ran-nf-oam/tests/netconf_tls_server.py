"""An in-process NETCONF-over-TLS server for tests (RFC 7589), with throwaway certificates, so `netconf_tls` is exercised without a container.

`Pki` makes a CA, a server certificate for 127.0.0.1 and client certificates signed by that CA (or by another one, to be refused). The server
requires a client certificate, then speaks the same NETCONF exchange as netconf_ssh_server's `_converse`."""

import datetime
import ipaddress
import socket
import ssl
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from netconf_ssh_server import Behaviour, _converse


def _write(path, data: bytes):
    path.write_bytes(data)
    return str(path)


class Pki:
    def __init__(self, directory, name="ca"):
        self.dir, self.name = directory, name
        self.key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"test {name}")])
        now = datetime.datetime.now(datetime.UTC)
        self.cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(self.key.public_key())
                     .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
                     .not_valid_after(now + datetime.timedelta(days=1))
                     .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                     .sign(self.key, hashes.SHA256()))
        self.ca_file = _write(directory / f"{name}.crt", self.cert.public_bytes(serialization.Encoding.PEM))

    def issue(self, cn: str, server: bool = False, expired: bool = False) -> tuple[str, str]:
        """(certificate file, key file) signed by this CA; a server certificate names 127.0.0.1."""
        key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.datetime.now(datetime.UTC)
        builder = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
                   .issuer_name(self.cert.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - datetime.timedelta(days=2 if expired else 0, minutes=1))
                   .not_valid_after(now - datetime.timedelta(days=1) if expired else now + datetime.timedelta(days=1)))
        if server:
            builder = builder.add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        builder = builder.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH]),
                                        critical=False)
        cert = builder.sign(self.key, hashes.SHA256())
        stem = f"{self.name}-{cn}"
        return (_write(self.dir / f"{stem}.crt", cert.public_bytes(serialization.Encoding.PEM)),
                _write(self.dir / f"{stem}.key", key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                                  serialization.NoEncryption())))


class NetconfTlsTestServer:
    def __init__(self, server_cert: str, server_key: str, client_ca: str, behaviour: Behaviour | None = None):
        self.behaviour = behaviour or Behaviour()
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(server_cert, server_key)
        self.context.verify_mode = ssl.CERT_REQUIRED
        self.context.load_verify_locations(cafile=client_ca)
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self.port = self._sock.getsockname()[1]
        self.handshakes_failed = 0
        self._running = True
        threading.Thread(target=self._accept, daemon=True).start()

    @property
    def uri(self) -> str:
        return f"tls://127.0.0.1:{self.port}"

    def _accept(self):
        while self._running:
            try:
                raw, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(raw,), daemon=True).start()

    def _serve(self, raw):
        try:
            conn = self.context.wrap_socket(raw, server_side=True)
        except (ssl.SSLError, OSError):
            self.handshakes_failed += 1
            raw.close()
            return
        try:
            _converse(conn, self.behaviour)
        except (OSError, ssl.SSLError):
            pass
        finally:
            conn.close()

    def close(self):
        self._running = False
        self._sock.close()
