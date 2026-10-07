#!/usr/bin/env python3
"""The verdict of the load that runs through an upgrade (PR-V-10): `scripts/upgrade_load_verdict.py load-results.json [--max-error-rate 0.01] [--min-calls 2000] [--report-only]`.

The load (scripts/load_run.py, paced, started before the first `helm upgrade` and stopped after the last step of the lane) must have run the whole time and must have
been answered: the error rate over the whole run is at most `--max-error-rate`, and no route other than the ones that restart with a gap (`--allow-errors-on`,
default `package list`: onboarding keeps its package store on a volume only one pod may hold and rolls with Recreate) answers more than `--max-route-errors` errors.
The errors by time (the 10 s slices that had any) are printed either way, so a failing run says when it happened: the migration, a module's rollout, the rollback.

Exit status 0 when the load was served, 1 when it was not (or did not run).
"""

import argparse
import json
import sys


def verdict(result: dict, max_error_rate: float = 0.01, min_calls: int = 2000, allow_errors_on: tuple[str, ...] = ("package list",),
            max_route_errors: int = 0) -> list[str]:
    """Why the run failed, one line each; an empty list when it passed."""
    total = result["total"]
    problems = []
    if total["requests"] < min_calls:
        problems.append(f"only {total['requests']} calls were made (at least {min_calls} expected): the load did not run through the upgrade")
    rate = total["errors"] / max(1, total["requests"])
    if rate > max_error_rate:
        problems.append(f"{total['errors']} of {total['requests']} calls were errors ({rate:.2%} > {max_error_rate:.2%})")
    for row in result["routes"]:
        if row["route"] not in allow_errors_on and row["errors"] > max_route_errors:
            problems.append(f"{row['route']}: {row['errors']} errors, statuses {row['statuses']} (this route is served through the whole upgrade)")
    return problems


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("results")
    ap.add_argument("--max-error-rate", type=float, default=0.01)
    ap.add_argument("--min-calls", type=int, default=2000)
    ap.add_argument("--max-route-errors", type=int, default=0)
    ap.add_argument("--report-only", action="store_true", help="print the verdict but exit 0: a jump over a release is outside the rolling guarantee")
    ap.add_argument("--allow-errors-on", action="append", default=None, help="a route that restarts with a gap (default: package list)")
    args = ap.parse_args(argv)
    result = json.load(open(args.results, encoding="utf-8"))
    bad = [row for row in result.get("timeline", []) if row["errors"]]
    print(f"{result['total']['requests']} calls over {result.get('seconds', '?')} s, {result['total']['errors']} errors")
    for row in bad:
        print(f"  {row['from_s']}-{row['to_s']} s: {row['errors']} errors of {row['calls']} calls")
    problems = verdict(result, args.max_error_rate, args.min_calls, tuple(args.allow_errors_on or ("package list",)), args.max_route_errors)
    for problem in problems:
        print(f"{'NOTE (report only)' if args.report_only else 'FAIL'}: {problem}", file=sys.stderr)
    return 1 if problems and not args.report_only else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
