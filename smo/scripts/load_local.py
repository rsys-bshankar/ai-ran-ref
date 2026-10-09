#!/usr/bin/env python3
"""The load run of PR-SEC-5.4 without Docker: a Postgres over TLS and six services as processes on this machine (PR-SEC-5.4b).

    scripts/load_local.py --out load-local [--duration 45] [--concurrency 8] [--ttl 30]

Starts a throwaway Postgres with TLS (the settings of docker-compose.pgtls.yml: scripts/pg_tls_check.py), migrates it, makes the module roles, and starts SME, DME, Onboarding, rApp
Management, RAN NF OAM and the R1 gateway with `PGSSLMODE=verify-full` and the development CA, so every database connection of the run is TLS and verified. Then it runs
scripts/load_run.py twice through the gateway, once with `R1_INTROSPECTION_CACHE_SECONDS=0` and once with `--ttl` (default 30), writes the results to `--out` (the first run) and
`--out/cache-<ttl>` (the second), and prints scripts/introspection_compare.py's table. Needs initdb and postgres (PostgreSQL server packages) and the runtime requirements.

What it is not: the compose stack. The services share this machine's cores with Postgres and the load generator, one worker each, so the absolute latencies are far above a real
deployment's and say nothing about sizing; the counts of introspections and the ratio between the two runs are the finding. `smo-load.yml` runs the same comparison on the compose stack.
"""

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SMO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SMO / "scripts"))

import httpx  # noqa: E402
import introspection_compare  # noqa: E402
import mtls_certs  # noqa: E402
import pg_tls_check  # noqa: E402

ROLE_MODULES = ("sme", "dme", "onboarding", "rapp-mgmt", "ran-nf-oam", "r1-termination")
PORTS = {"sme": 8101, "dme": 8102, "onboarding": 8103, "rapp-mgmt": 8104, "ran-nf-oam": 8105}
GATEWAY_PORT = 8100


class Stack:
    """The throwaway local stack of one run: a TLS Postgres from `pg_tls_check.LocalServer`, the module processes, and the files they share in `work`.

        `shutdown` must be called (the caller's `finally` does) or the Postgres and the uvicorn processes keep running. Nothing here touches the compose stack or a real database.
    """
    def __init__(self, work: Path):
        """Prepares the paths under `work` (secrets, the CA) and the fixed throwaway password of the database owner; starts nothing."""
        self.work = work
        self.procs: dict[str, subprocess.Popen] = {}
        self.server: pg_tls_check.LocalServer | None = None
        self.secrets = work / "secrets"
        self.ca = work / "certs" / "postgres" / "ca.crt"
        self.password = "local-load-owner-password"          # noqa: S105 (a throwaway database on this machine)

    def env(self, name: str | None, extra: dict | None = None) -> dict:
        """The environment of one child process (`name` a module, or None for the migration and role scripts that run as the database owner).

            Every process gets `PGSSLMODE=verify-full` with the development CA, a small connection pool, the loopback URLs of the other modules, and the gateway's audit and
            rate limit switched off (`R1_AUDIT=off`, `R1_RATE_PER_SECOND=0`) so the measurement is not shaped by them. A module gets its own database role and password file;
            `extra` is applied last and wins. Requires `start_database` to have run (the port of the server is read).
        """
        assert self.server
        env = {**os.environ, "PGSSLMODE": "verify-full", "PGSSLROOTCERT": str(self.ca), "PYTHONUNBUFFERED": "1", "SMO_ENROLLMENT_SECRET_FILE": str(self.work / "enroll"),
               "SMO_DB_POOL_SIZE": "10", "R1_AUDIT": "off", "R1_RATE_PER_SECOND": "0", "R1_KILL_SWITCH_SCHEMA": "ran_nf_oam", "R1_GATEWAY_URL": f"http://127.0.0.1:{GATEWAY_PORT}",
               "SME_URL": f"http://127.0.0.1:{PORTS['sme']}", "DME_URL": f"http://127.0.0.1:{PORTS['dme']}", "ONBOARDING_URL": f"http://127.0.0.1:{PORTS['onboarding']}",
               "RAPP_MGMT_URL": f"http://127.0.0.1:{PORTS['rapp-mgmt']}", "RAN_NF_OAM_URL": f"http://127.0.0.1:{PORTS['ran-nf-oam']}"}
        host = f"127.0.0.1:{self.server.port}"
        if name:
            env |= {"SMO_DATABASE_URL": f"postgresql+psycopg://smo_{name.replace('-', '_')}@{host}/smo", "SMO_DATABASE_PASSWORD_FILE": str(self.secrets / f"db_password_{name}"),
                    "PYTHONPATH": f"{SMO / name}:{SMO / 'shared'}:{SMO / 'sdk'}", "MODULE": name}
        else:
            env |= {"SMO_DATABASE_URL": f"postgresql+psycopg://smo:{self.password}@{host}/smo"}
        return env | (extra or {})

    def start_database(self) -> None:
        """Makes the development CA, starts the TLS Postgres, creates the `smo` database, writes the enrollment and per-module password files, then runs
            `migrate.py` and `db_roles.py` against it as the owner.

            Raises `SystemExit` with the last 800 bytes of stderr when either script fails.
        """
        import psycopg
        mtls_certs.init(self.work / "certs", 30)
        (self.work / "pg").mkdir()
        self.server = pg_tls_check.LocalServer(pg_tls_check.find_pg_bin(), self.work / "pg", self.work / "certs", self.password)
        self.server.start()
        with psycopg.connect(host="127.0.0.1", port=self.server.port, user="smo", password=self.password, dbname="postgres", sslmode="verify-full", sslrootcert=str(self.ca),
                             autocommit=True) as conn:
            conn.execute("CREATE DATABASE smo")
        (self.work / "enroll").write_text("local-load-enrollment-secret")
        self.secrets.mkdir()
        for module in ROLE_MODULES:
            (self.secrets / f"db_password_{module}").write_text(f"local-{module}-password")
        for script, extra in (("migrate.py", {}), ("db_roles.py", {"SMO_DB_ROLE_PASSWORD_DIR": str(self.secrets)})):
            run = subprocess.run([sys.executable, str(SMO / "scripts" / script)], env=self.env(None, extra), capture_output=True, text=True, cwd=SMO)  # noqa: S603
            if run.returncode:
                raise SystemExit(f"{script} failed: {run.stderr[-800:]}")

    def start(self, name: str, port: int, extra: dict | None = None) -> None:
        """Starts one module as `uvicorn app.main:app` on `port` (loopback only, from the module's directory), logging to `<work>/<name>.log`. Does not wait for it; see `wait_ready`."""
        log = (self.work / f"{name}.log").open("w")
        self.procs[name] = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],   # noqa: S603
                                            cwd=SMO / name, env=self.env(name, extra), stdout=log, stderr=subprocess.STDOUT)

    def stop(self, name: str) -> None:
        """Stops a started module with SIGTERM, and kills it if it has not exited after 15 s. Unknown names are ignored."""
        proc = self.procs.pop(name, None)
        if proc:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                proc.kill()

    def wait_ready(self, port: int, timeout: float = 90) -> bool:
        """Polls `/ready` of the service on `port` every half second; True as soon as it answers 200, False after `timeout` seconds."""
        end = time.time() + timeout
        while time.time() < end:
            try:
                if httpx.get(f"http://127.0.0.1:{port}/ready", timeout=2).status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        return False

    def session_counts(self) -> tuple[int, int]:
        """The sessions the services hold open on the database: how many are over TLS, how many are not (the second must be 0)."""
        import psycopg
        assert self.server
        with psycopg.connect(host="127.0.0.1", port=self.server.port, user="smo", password=self.password, dbname="smo", sslmode="verify-full", sslrootcert=str(self.ca)) as conn:
            row = conn.execute("select count(*) filter (where s.ssl), count(*) filter (where not s.ssl) from pg_stat_ssl s join pg_stat_activity a using (pid) "
                               "where a.datname = 'smo' and a.pid <> pg_backend_pid()").fetchone()
        return (row[0], row[1]) if row else (0, 0)

    def shutdown(self) -> None:
        """Stops every module still running, then the Postgres. Safe to call after a partial start."""
        for name in list(self.procs):
            self.stop(name)
        if self.server:
            self.server.stop()


def main(argv: list[str] | None = None) -> int:
    """Runs the whole comparison and returns `introspection_compare`'s exit code (1 when the comparison found a problem).

        Order: database, SME, the gateway, the other modules; then for each of the two runs (cache off, cache `--ttl`) the gateway is restarted with that
        `R1_INTROSPECTION_CACHE_SECONDS` and `load_run.py` is run through it. A load run that exits non-zero is printed but does not stop the script. Raises `SystemExit`
        when PostgreSQL's `initdb`/`postgres` are not installed or a service does not become ready; logs are then left in the work directory named in the message.
        The work directory is removed at the end in every other case.
    """
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=Path("load-local"))
    ap.add_argument("--duration", type=float, default=45)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--ttl", type=int, default=30, help="R1_INTROSPECTION_CACHE_SECONDS of the second run")
    args = ap.parse_args(argv)
    if pg_tls_check.find_pg_bin() is None:
        raise SystemExit("no initdb/postgres on this machine: this script needs the PostgreSQL server packages (the compose lane smo-load.yml runs the same comparison in Docker)")
    work = Path(tempfile.mkdtemp(prefix="smo-load-local-"))
    os.chmod(work, 0o755)  # noqa: S103 (the database user must traverse it; a throwaway directory)
    stack = Stack(work)
    try:
        stack.start_database()
        stack.start("sme", PORTS["sme"])
        stack.wait_ready(PORTS["sme"])
        stack.start("r1-termination", GATEWAY_PORT, {"R1_INTROSPECTION_CACHE_SECONDS": "0"})
        stack.wait_ready(GATEWAY_PORT)
        for name, port in PORTS.items():
            if name != "sme":
                stack.start(name, port)
        not_ready = [name for name, port in PORTS.items() if not stack.wait_ready(port)]
        if not_ready:
            raise SystemExit(f"not ready: {not_ready}; logs in {work}")
        print("database sessions (over TLS, plain):", stack.session_counts(), flush=True)
        outputs = {}
        for label, ttl in (("off", 0), (f"cache-{args.ttl}", args.ttl)):
            stack.stop("r1-termination")
            stack.start("r1-termination", GATEWAY_PORT, {"R1_INTROSPECTION_CACHE_SECONDS": str(ttl)})
            if not stack.wait_ready(GATEWAY_PORT):
                raise SystemExit(f"the gateway did not come back; logs in {work}")
            out = args.out if ttl == 0 else args.out / label
            out.mkdir(parents=True, exist_ok=True)
            run = subprocess.run([sys.executable, str(SMO / "scripts" / "load_run.py"), "--gateway", f"http://127.0.0.1:{GATEWAY_PORT}", "--sme", f"http://127.0.0.1:{PORTS['sme']}",  # noqa: S603
                                  "--duration", str(args.duration), "--warmup", "5", "--concurrency", str(args.concurrency), "--out", str(out)],
                                 env={**os.environ, "SMO_ENROLLMENT_SECRET_FILE": str(work / "enroll")}, capture_output=True, text=True, cwd=SMO)
            print(f"[{label}] exit {run.returncode}\n{run.stdout}{run.stderr[-600:]}", flush=True)
            outputs[label] = out
        return introspection_compare.main(["--off", str(outputs["off"]), "--on", str(outputs[f"cache-{args.ttl}"]), "--seconds", str(args.ttl),
                                           "--out", str(args.out / "introspection-cache.md")])
    finally:
        stack.shutdown()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
