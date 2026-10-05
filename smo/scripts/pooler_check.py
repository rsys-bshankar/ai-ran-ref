"""PR-DB-5.1/5.2: the stack behind PgBouncer in transaction mode.

    docker compose exec -T sme python3 - < scripts/pooler_check.py

Runs inside the SME container (which holds the password of its own role) on the compose network, with the stack started as
`SMO_DB_HOST=pgbouncer SMO_DB_PORT=6432 SMO_DB_POOLER=transaction docker compose --profile pooler up -d`. It checks that

  - 60 client connections held open at once cost Postgres no more than the pool size (the pooler is in the path and pools);
  - the service's own engine (smo_shared.db, with SMO_DB_POOLER set, so no prepared statements) runs the same statements from many
    threads without "prepared statement ... does not exist";
  - prepared statements, switched on at the driver (prepare_threshold=0, every statement prepared at once), also work through this
    PgBouncer (max_prepared_statements), so SMO_DB_PREPARE_THRESHOLD can turn them back on.

Exits 1 on the first failure.
"""
import os
import sys
import threading
import time

import psycopg
from sqlalchemy import text

from smo_shared.db import build_engine

POOL = int(os.environ.get("PGBOUNCER_POOL_SIZE", "20"))
PASSWORD = open("/run/secrets/db_password_sme", encoding="utf-8").read().strip()
HOST, PORT = os.environ.get("SMO_DB_HOST", "pgbouncer"), os.environ.get("SMO_DB_PORT", "6432")
failures: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        failures.append(name)


def connect(**kw) -> psycopg.Connection:
    return psycopg.connect(host=HOST, port=PORT, dbname="smo", user="smo_sme", password=PASSWORD, autocommit=True, **kw)


# 1. many clients, few servers: every client runs one statement (so PgBouncer gives it a server connection for that transaction) and stays connected
clients = [connect() for _ in range(60)]
for c in clients:
    c.execute("SELECT 1")
direct = psycopg.connect(host="postgres", port=5432, dbname="smo", user="smo_sme", password=PASSWORD, autocommit=True)
servers = direct.execute("SELECT count(*) FROM pg_stat_activity WHERE usename = 'smo_sme' AND state IS NOT NULL AND pid <> pg_backend_pid()").fetchone()[0]
check(f"60 clients through the pooler use at most {POOL} server connections (saw {servers})", servers <= POOL + 2, servers)
for c in clients:
    c.close()
direct.close()

# 2. the service's own engine, from many threads
errors: list[str] = []


def hammer(engine, n: int) -> None:
    try:
        for i in range(n):
            with engine.connect() as conn:
                assert conn.execute(text("SELECT :a::int + 1"), {"a": i}).scalar() == i + 1
                conn.execute(text("SELECT count(*) FROM information_schema.tables"))
    except Exception as exc:                                  # noqa: BLE001
        errors.append(f"{type(exc).__name__}: {exc}"[:300])


engine = build_engine(f"postgresql+psycopg://smo_sme@{HOST}:{PORT}/smo", environ={**os.environ, "SMO_DATABASE_PASSWORD": PASSWORD, "SMO_DB_POOLER": "transaction"})
threads = [threading.Thread(target=hammer, args=(engine, 60)) for _ in range(12)]
started = time.monotonic()
[t.start() for t in threads]
[t.join() for t in threads]
check(f"the service's engine ran 720 statement pairs from 12 threads without an error ({time.monotonic() - started:.1f} s)", not errors, errors[:2])
engine.dispose()

# 3. prepared statements at the driver, through this PgBouncer
errors.clear()


def prepared(n: int) -> None:
    try:
        with connect(prepare_threshold=0) as conn:
            for i in range(n):
                assert conn.execute("SELECT %s::int + 1", (i,)).fetchone()[0] == i + 1
    except Exception as exc:                                  # noqa: BLE001
        errors.append(f"{type(exc).__name__}: {exc}"[:300])


threads = [threading.Thread(target=prepared, args=(100,)) for _ in range(12)]
[t.start() for t in threads]
[t.join() for t in threads]
check("prepared statements (prepare_threshold=0) from 12 clients work through PgBouncer's max_prepared_statements", not errors, errors[:2])

print()
if failures:
    print(f"{len(failures)} pooler check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("all pooler checks passed")
