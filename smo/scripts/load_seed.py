#!/usr/bin/env python3
"""Seed the SMO database with volume for the load run (PR-V-8b): N managed elements, 10 alarms and 5 performance files for each.

    scripts/load_seed.py --elements 10000 [--host postgres] [--password-file /run/secrets/db_password]

Connects as the database owner (`smo`) and loads with COPY, so 100 000 elements (a million alarms) take about a minute. The rows are plainly synthetic: element
references `load-me-<n>`, source alarm ids `load-<n>-<k>`. `--clean` removes them again. Run it from the one-shot container the load workflow uses, on the compose network.
"""

import argparse
import datetime
import json
import random
import uuid
from pathlib import Path

import psycopg

SEVERITIES = ["critical", "major", "minor", "warning", "indeterminate", "cleared"]
ALARMS_PER_ELEMENT = 10
PM_FILES_PER_ELEMENT = 5


def connect(args) -> psycopg.Connection:
    password = Path(args.password_file).read_text(encoding="utf-8").strip()
    return psycopg.connect(host=args.host, port=args.port, dbname="smo", user="smo", password=password, autocommit=False)


def clean(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM ran_nf_oam.alarm WHERE managed_element_ref LIKE 'load-me-%'")
        cur.execute("DELETE FROM ran_nf_oam.pm_file WHERE managed_element_ref LIKE 'load-me-%'")
        cur.execute("DELETE FROM ran_nf_oam.managed_entity WHERE managed_element_ref LIKE 'load-me-%'")
    conn.commit()


def seed(conn, elements: int, seed_value: int) -> dict:
    rng = random.Random(seed_value)  # noqa: S311 — synthetic data, not a secret
    now = datetime.datetime.now(datetime.UTC)
    with conn.cursor() as cur:
        with cur.copy("COPY ran_nf_oam.managed_entity (managed_element_ref, entity_type, vendor_name, o1_protocol, cell_guards) FROM STDIN") as cp:
            for n in range(elements):
                cp.write_row((f"load-me-{n}", "O-DU", "load-vendor", "NETCONF", json.dumps({})))
        with cur.copy("COPY ran_nf_oam.alarm (alarm_id, source_alarm_id, managed_element_ref, severity, ack_state, raised_at, root_cause_indicator, "
                      "correlated_notifications) FROM STDIN") as cp:
            for n in range(elements):
                for k in range(ALARMS_PER_ELEMENT):
                    raised = now - datetime.timedelta(seconds=rng.randint(0, 30 * 86400))
                    cp.write_row((str(uuid.uuid4()), f"load-{n}-{k}", f"load-me-{n}", rng.choice(SEVERITIES),
                                  rng.choice(["UNACKNOWLEDGED", "ACKNOWLEDGED"]), raised, False, "{}"))
        with cur.copy("COPY ran_nf_oam.pm_file (file_id, managed_element_ref, counter_type, file_data_type, file_format, content, file_size, file_ready_time) FROM STDIN") as cp:
            for n in range(elements):
                for k in range(PM_FILES_PER_ELEMENT):
                    content = json.dumps({"cell": f"c{k}", "dlPrbUsage": rng.randint(0, 100), "rrcConnMean": rng.randint(0, 500)})
                    cp.write_row((str(uuid.uuid4()), f"load-me-{n}", "dlPrbUsage", "Performance", "json", content, len(content),
                                  now - datetime.timedelta(minutes=15 * k)))
        cur.execute("ANALYZE ran_nf_oam.managed_entity")
        cur.execute("ANALYZE ran_nf_oam.alarm")
        cur.execute("ANALYZE ran_nf_oam.pm_file")
    conn.commit()
    return {"elements": elements, "alarms": elements * ALARMS_PER_ELEMENT, "pm_files": elements * PM_FILES_PER_ELEMENT}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--elements", type=int, default=1000)
    ap.add_argument("--host", default="postgres")
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--password-file", default="/run/secrets/db_password")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--clean", action="store_true", help="remove the rows this script made, and exit")
    args = ap.parse_args()
    with connect(args) as conn:
        clean(conn)
        if not args.clean:
            print(json.dumps(seed(conn, args.elements, args.seed)))


if __name__ == "__main__":
    main()
