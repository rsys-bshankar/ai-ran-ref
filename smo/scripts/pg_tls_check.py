#!/usr/bin/env python3
"""Proves that the clients of Postgres verify its certificate (PR-SEC-2.4).

    scripts/pg_tls_check.py --host postgres --ca certs/mtls/postgres/ca.crt --password-file /run/secrets/db_password          against a running server
    scripts/pg_tls_check.py --spawn [--keep-dir DIR]                                                                         a throwaway local server of its own (needs initdb, postgres)

What it asks of the server at `--host`, with `sslmode=verify-full`, as the owner role `--user` (default `smo`):

  1. the right CA connects, and the server reports the session as encrypted (`pg_stat_ssl`);
  2. a CA that did not sign the server's certificate FAILS (a made-up one, so the check needs no second PKI);
  3. the right CA but another host name FAILS (`hostaddr` keeps the connection going to the same server; the certificate does not name the other one);
  4. with `--expect-plain-refused` (docker-compose.pgtls.yml and the chart's `postgres.tls.enabled` serve `hostssl` only): `sslmode=disable` FAILS.

Exit status 0 when every expected outcome happened, 1 otherwise; each line says which. `--spawn` makes the certificates with scripts/mtls_certs.py, starts a Postgres on a
free port with the same settings the compose overlay gives the container (ssl=on, a pg_hba.conf with `hostssl` only), runs the four checks against it and stops it.
It is what the unit test (tests_integration/test_pg_tls.py) and a workstation use; the compose lane runs it against the real container.
"""

import argparse
import ipaddress
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

PG_HBA = (SCRIPTS.parent / "pgtls" / "pg_hba.conf").read_text(encoding="utf-8")      # the file the compose overlay mounts


def _connect(**kwargs):
    import psycopg
    return psycopg.connect(connect_timeout=5, **kwargs)


def _fake_ca(directory: Path) -> Path:
    """A CA that signed nothing the server holds: written to `directory`/other-ca.crt."""
    import mtls_certs
    cert, _key = mtls_certs.make_ca(days=1, common_name="not the database's CA")
    path = directory / "other-ca.crt"
    path.write_bytes(mtls_certs.cert_pem(cert))
    return path


def run_checks(host: str, port: int, user: str, password: str, database: str, ca: Path, expect_plain_refused: bool, workdir: Path) -> list[tuple[bool, str]]:
    base = {"host": host, "port": port, "user": user, "password": password, "dbname": database}
    results: list[tuple[bool, str]] = []

    def record(ok: bool, text: str) -> None:
        results.append((ok, text))

    try:
        with _connect(**base, sslmode="verify-full", sslrootcert=str(ca)) as conn:
            encrypted = conn.execute("select ssl from pg_stat_ssl where pid = pg_backend_pid()").fetchone()
            record(bool(encrypted and encrypted[0]), "verify-full with the database's CA connects over TLS" if encrypted and encrypted[0]
                   else "connected, but pg_stat_ssl says the session is not encrypted")
    except Exception as error:   # noqa: BLE001 (any failure is the finding)
        record(False, f"verify-full with the database's CA did not connect: {error}")

    other = _fake_ca(workdir)
    try:
        _connect(**base, sslmode="verify-full", sslrootcert=str(other)).close()
        record(False, "verify-full with a CA that did not sign the certificate CONNECTED (it must fail)")
    except Exception as error:   # noqa: BLE001
        message = str(error)
        record("certificate verify failed" in message or "unable to get local issuer" in message or "self-signed" in message or "self signed" in message,
               f"verify-full with another CA is refused: {message.strip().splitlines()[0][:160]}")

    try:
        address = host if _is_ip(host) else socket.gethostbyname(host)
        _connect(**{**base, "host": "not-the-database.invalid"}, hostaddr=address, sslmode="verify-full", sslrootcert=str(ca)).close()
        record(False, "verify-full against a host name the certificate does not carry CONNECTED (it must fail)")
    except Exception as error:   # noqa: BLE001
        message = str(error)
        record("does not match host name" in message or "hostname" in message.lower(), f"verify-full with another host name is refused: {message.strip().splitlines()[0][:160]}")

    if expect_plain_refused:
        try:
            _connect(**base, sslmode="disable").close()
            record(False, "sslmode=disable CONNECTED (the server must accept TLS only)")
        except Exception as error:   # noqa: BLE001
            record(True, f"sslmode=disable is refused: {str(error).strip().splitlines()[0][:160]}")
    return results


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


# ------------------------------------------------------------------------------------------------------------------------ a throwaway server

def find_pg_bin() -> Path | None:
    """The directory with initdb and postgres: on the PATH, or the newest /usr/lib/postgresql/<n>/bin."""
    found = shutil.which("initdb")
    if found and shutil.which("postgres"):
        return Path(found).parent
    candidates = sorted(Path("/usr/lib/postgresql").glob("*/bin"), key=lambda p: int(p.parent.name) if p.parent.name.isdigit() else 0, reverse=True)
    for directory in candidates:
        if (directory / "initdb").exists() and (directory / "postgres").exists():
            return directory
    return None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LocalServer:
    """initdb + postgres in a temporary directory, with ssl on and a hostssl-only pg_hba.conf, as the compose overlay runs it.
    Postgres refuses to run as root, so as root it runs as uid 65534 (nobody) and the directory is handed to it."""

    def __init__(self, pg_bin: Path, base: Path, certs: Path, password: str):
        self.pg_bin, self.base, self.certs, self.password = pg_bin, base, certs, password
        self.port = _free_port()
        self.process: subprocess.Popen | None = None
        self._uid = 65534 if os.geteuid() == 0 else None

    def _run(self, *argv: str, **kwargs):
        def drop():
            if self._uid is not None:
                os.setgid(self._uid)
                os.setuid(self._uid)
        return subprocess.run(argv, check=True, capture_output=True, text=True, preexec_fn=drop if self._uid is not None else None, **kwargs)  # noqa: S603

    def start(self) -> None:
        data = self.base / "data"
        (self.base / "pw").write_text(self.password)
        ssl_dir = self.base / "ssl"
        ssl_dir.mkdir()
        for name, mode in (("tls.crt", 0o644), ("tls.key", 0o600)):
            shutil.copyfile(self.certs / "postgres" / name, ssl_dir / name)       # the key must be 0600 and the server's own: the compose overlay does the same
            os.chmod(ssl_dir / name, mode)
        (self.base / "pg_hba.conf").write_text(PG_HBA)
        if self._uid is not None:
            for path in (self.base, *self.base.rglob("*")):
                os.chown(path, self._uid, self._uid)
            os.chmod(self.base, 0o755)  # noqa: S103 (the unprivileged server user must traverse it; a throwaway directory)
        self._run(str(self.pg_bin / "initdb"), "-D", str(data), "-U", "smo", "--auth-host=scram-sha-256", "--auth-local=trust", f"--pwfile={self.base / 'pw'}", "-E", "UTF8")
        self.process = subprocess.Popen(                                                     # noqa: S603
            [str(self.pg_bin / "postgres"), "-D", str(data), "-p", str(self.port), "-c", "listen_addresses=127.0.0.1", "-c", f"unix_socket_directories={self.base}",
             "-c", "ssl=on", "-c", f"ssl_cert_file={ssl_dir / 'tls.crt'}", "-c", f"ssl_key_file={ssl_dir / 'tls.key'}", "-c", f"hba_file={self.base / 'pg_hba.conf'}"],
            stdout=subprocess.DEVNULL, stderr=(self.base / "postgres.log").open("w"), preexec_fn=(lambda: (os.setgid(self._uid), os.setuid(self._uid))) if self._uid is not None else None)  # type: ignore[arg-type]  # noqa: PLW1509
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    pass
                time.sleep(0.5)
                return
            except OSError:
                if self.process.poll() is not None:
                    raise RuntimeError("postgres exited: " + (self.base / "postgres.log").read_text()) from None
                time.sleep(0.2)
        raise RuntimeError("postgres did not start in 30 s")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(20)
            except subprocess.TimeoutExpired:
                self.process.kill()


def spawn_and_check(keep_dir: Path | None = None) -> list[tuple[bool, str]]:
    import mtls_certs
    pg_bin = find_pg_bin()
    if pg_bin is None:
        raise SystemExit("no initdb/postgres found: install PostgreSQL or run this against a running server with --host")
    with tempfile.TemporaryDirectory(prefix="smo-pgtls-") as tmp:
        root = Path(tmp)
        os.chmod(root, 0o755)  # noqa: S103 (as above)
        certs = root / "certs"
        mtls_certs.init(certs, 30)
        server = LocalServer(pg_bin, root / "pg", certs, "pg-tls-check-password")
        (root / "pg").mkdir()
        try:
            server.start()
            results = run_checks("127.0.0.1", server.port, "smo", server.password, "postgres", certs / "postgres" / "ca.crt", True, root)
        finally:
            server.stop()
            if keep_dir:
                shutil.copytree(root, keep_dir, dirs_exist_ok=True)
        return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--spawn", action="store_true", help="start a throwaway local Postgres with TLS and check that")
    ap.add_argument("--keep-dir", type=Path, default=None, help="with --spawn: copy the working directory (certificates, server log) here afterwards")
    ap.add_argument("--host", default="postgres")
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--user", default="smo")
    ap.add_argument("--database", default="smo")
    ap.add_argument("--password-file", default=os.environ.get("SMO_DATABASE_PASSWORD_FILE", "/run/secrets/db_password"))
    ap.add_argument("--ca", type=Path, default=Path("certs/mtls/postgres/ca.crt"))
    ap.add_argument("--expect-plain-refused", action="store_true", help="also require that sslmode=disable is refused (the server serves hostssl only)")
    args = ap.parse_args(argv)
    if args.spawn:
        results = spawn_and_check(args.keep_dir)
    else:
        password = Path(args.password_file).read_text(encoding="utf-8").strip()
        with tempfile.TemporaryDirectory() as tmp:
            results = run_checks(args.host, args.port, args.user, password, args.database, args.ca, args.expect_plain_refused, Path(tmp))
    for ok, text in results:
        print(("ok   " if ok else "FAIL ") + text)
    return 0 if all(ok for ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
