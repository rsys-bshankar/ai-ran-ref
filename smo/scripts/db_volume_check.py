#!/usr/bin/env python3
"""Query plans and timings on a large database (PR-V-6).

    scripts/db_volume_check.py [--host localhost] [--password smo] [--out volume-results.md]

Run after `scripts/load_seed.py --elements 100000` (1 000 000 alarms, 500 000 performance files, 100 000 elements). Each case is `EXPLAIN (ANALYZE, FORMAT JSON)` of the
query a list route runs; the table says how long it took and which scans it used. Cases marked `gate` fail the run when they are slower than their budget, or (the ones that
must use an index) when they scan the table; the others are reported, so a regression is a difference between two tables. Exit status 1 if a gate failed.
"""

import argparse
import sys

import psycopg

# (name, sql, gate budget in ms or None, must use an index)
CASES = [
    ("alarms of one element", "SELECT * FROM ran_nf_oam.alarm WHERE managed_element_ref = 'load-me-77777' LIMIT 100", 100, True),
    ("alarm count of one element (the page total)", "SELECT count(*) FROM (SELECT * FROM ran_nf_oam.alarm WHERE managed_element_ref = 'load-me-77777') s", 100, True),
    ("first page of all alarms", "SELECT * FROM ran_nf_oam.alarm LIMIT 100 OFFSET 0", 100, False),
    ("a deep page of all alarms", "SELECT * FROM ran_nf_oam.alarm LIMIT 100 OFFSET 500000", None, False),
    ("alarm count (the total every unfiltered page asks for unless it sends ?total=false)", "SELECT count(*) FROM (SELECT * FROM ran_nf_oam.alarm) s", 5000, False),
    ("alarms of one severity, first page", "SELECT * FROM ran_nf_oam.alarm WHERE severity = 'critical' LIMIT 100", 500, False),
    ("alarm count of one severity", "SELECT count(*) FROM (SELECT * FROM ran_nf_oam.alarm WHERE severity = 'critical') s", None, False),
    ("performance files of one element", "SELECT * FROM ran_nf_oam.pm_file WHERE managed_element_ref = 'load-me-77777' LIMIT 100", 100, True),
    ("managed elements, first page", "SELECT * FROM ran_nf_oam.managed_entity LIMIT 100 OFFSET 0", 100, False),
    ("managed element count", "SELECT count(*) FROM (SELECT * FROM ran_nf_oam.managed_entity) s", 1000, False),
]


def nodes(plan: dict) -> list[dict]:
    found = [plan]
    for child in plan.get("Plans", []):
        found += nodes(child)
    return found


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--password", default="smo")
    ap.add_argument("--out", default="volume-results.md")
    args = ap.parse_args()
    rows, failed = [], False
    with psycopg.connect(host=args.host, port=args.port, dbname="smo", user="smo", password=args.password, autocommit=True) as conn:
        sizes = conn.execute("SELECT relname, reltuples::bigint, pg_size_pretty(pg_total_relation_size(c.oid)) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                             "WHERE n.nspname = 'ran_nf_oam' AND relname IN ('alarm', 'pm_file', 'managed_entity') ORDER BY relname").fetchall()
        for name, sql, budget, needs_index in CASES:
            conn.execute("SELECT 1")                                                  # a warm connection; the first run of each also warms the cache
            conn.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + sql)
            plan = conn.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + sql).fetchone()[0][0]
            ms = round(plan["Execution Time"], 1)
            types = sorted({n["Node Type"] for n in nodes(plan["Plan"]) if "Scan" in n["Node Type"]})
            seq = any(t == "Seq Scan" for t in types)
            verdict = "report"
            if budget is not None:
                verdict = "ok"
                if ms > budget:
                    verdict, failed = f"SLOW (budget {budget} ms)", True
            if needs_index and seq:
                verdict, failed = "SEQ SCAN (must use an index)", True
            rows.append((name, ms, ", ".join(types), verdict))
    lines = ["Database size:", "", "| table | rows | total size |", "|---|---:|---|"] + [f"| {n} | {r} | {s} |" for n, r, s in sizes]
    lines += ["", "| query | ms | scans | verdict |", "|---|---:|---|---|"] + [f"| {n} | {ms} | {t} | {v} |" for n, ms, t, v in rows]
    text = "\n".join(lines) + "\n"
    print(text)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
