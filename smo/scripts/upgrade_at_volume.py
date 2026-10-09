#!/usr/bin/env python3
"""Applies every schema revision after `--from-revision` to a database that already holds data, one at a time, and times each (PR-V-6, the migration of a large table).

    scripts/upgrade_at_volume.py --url postgresql+psycopg://... --from-revision 0026 [--budget-seconds 120] [--out summary.md]

The database is at `--from-revision` (the previous release's head) and loaded with volume (`scripts/load_seed.py --database`), so a revision that scans or rewrites a big
table shows up in its own row: an index on half a million performance files, a column with a default on a table of a million rows. Each revision is one
`scripts/migrate.py --revision <id>` run. Exit status 1 if a revision fails or the whole upgrade takes longer than `--budget-seconds`: the number is what an operator
plans a maintenance window from, and a revision that makes it jump is a finding, not a flake. Writes a Markdown table to `--out` and stdout.
"""

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SMO_ROOT = Path(__file__).resolve().parent.parent
VERSIONS = SMO_ROOT / "migrations" / "versions"


def revisions_after(versions: Path, from_revision: str) -> list[str]:
    """The ids of the revisions in `versions` that come after `from_revision`, oldest first (the ids are the zero-padded numbers the files start with)."""
    found = sorted({m.group(1) for path in versions.glob("*.py") if (m := re.match(r"^(\d+)_", path.name))})
    if from_revision not in found:
        raise SystemExit(f"revision {from_revision} is not in {versions}")
    return found[found.index(from_revision) + 1:]


def failure_line(text: str) -> str:
    """The line of a traceback that names the error (the last one with `Error` or `error:` in it), not the library's closing hint."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    named = [line for line in lines if re.search(r"Error|error:", line) and not line.startswith("(Background on this error")]
    return (named or lines or ["no output"])[-1][:160]


def markdown(rows: list[tuple[str, float, str]], total: float, budget: float) -> str:
    """A markdown table of the revisions with their times and notes, closing with a total row that states the budget."""
    lines = ["| Revision | Seconds | |", "|---|---:|---|"]
    lines += [f"| {revision} | {seconds:.1f} | {note} |" for revision, seconds, note in rows]
    lines += [f"| **all** | **{total:.1f}** | budget {budget:.0f} s |"]
    return "\n".join(lines) + "\n"


def run_upgrade(url: str, revisions: list[str], runner=subprocess.run, clock=time.perf_counter) -> tuple[list[tuple[str, float, str]], bool]:
    """Applies the revisions in order; returns (id, seconds, note) per revision and whether all of them applied."""
    env = {**os.environ, "SMO_DATABASE_URL": url}
    rows: list[tuple[str, float, str]] = []
    for revision in revisions:
        started = clock()
        done = runner([sys.executable, str(SMO_ROOT / "scripts" / "migrate.py"), "--revision", revision], env=env, capture_output=True, text=True, cwd=SMO_ROOT)
        seconds = clock() - started
        if done.returncode != 0:
            rows.append((revision, seconds, "FAILED: " + failure_line(done.stderr or done.stdout)))
            return rows, False
        rows.append((revision, seconds, ""))
    return rows, True


def main(argv: list[str] | None = None) -> int:
    """Times the upgrade from `--from-revision` to this checkout's head and returns 1 if there is nothing to apply, a revision failed, or the total is over `--budget-seconds`.

        Prints the table, and writes it to `--out` when given. The database named by `--url` is changed; point it at a throwaway one.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", required=True)
    ap.add_argument("--from-revision", required=True)
    ap.add_argument("--budget-seconds", type=float, default=120)
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    revisions = revisions_after(VERSIONS, args.from_revision)
    if not revisions:
        print(f"nothing after {args.from_revision}: the database is at this checkout's head already", file=sys.stderr)
        return 1
    rows, applied = run_upgrade(args.url, revisions)
    total = sum(seconds for _, seconds, _ in rows)
    table = markdown(rows, total, args.budget_seconds)
    print(table)
    if args.out:
        Path(args.out).write_text(table, encoding="utf-8")
    if not applied:
        return 1
    if total > args.budget_seconds:
        print(f"FAIL: the upgrade took {total:.1f} s, over the budget of {args.budget_seconds:.0f} s", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
