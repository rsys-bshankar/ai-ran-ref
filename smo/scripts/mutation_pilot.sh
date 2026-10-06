#!/usr/bin/env bash
# PR-V-2c: a mutation-testing pilot on the small, security-relevant parts of the shared library.
#
#   scripts/mutation_pilot.sh
#
# mutmut changes the source one small step at a time (a `>` becomes `>=`, a `*` becomes `/`, a condition becomes false) and runs the tests against each change; a
# change no test notices ("survived", or "no tests": nothing runs that code) is a place where a bug could be introduced and every test would still pass. Coverage says
# a line ran; this says whether a test would notice if it were wrong.
#
# Scope (smo/shared/pyproject.toml, [tool.mutmut]): pagination.py, ratelimit.py, timeutil.py and roles.py (the gateway's role policy and the enrollment-secret check),
# tested by shared/tests and the gateway's own tests/test_roles.py. Fails unless every mutant is killed. The first run (October 2026) killed 49 of 131 and found
# 5 survivors and 77 mutants no test reached; the tests added for it (shared/tests/test_pagination.py, test_roles_helpers.py, test_timeutil.py, two in test_ratelimit.py)
# kill all 131. A mutant that cannot be killed (equivalent to the original) is listed in `do_not_mutate` with the reason, not left to fail the run.
#
# Needs the dev requirements installed (requirements/dev.txt) and mutmut (requirements/mutation.txt). About a minute.
set -euo pipefail
cd "$(dirname "$0")/../shared"
rm -rf mutants
export PYTHONPATH=".:../r1-termination"
mutmut run > /tmp/mutmut-run.txt 2>&1 || { tr '\r' '\n' < /tmp/mutmut-run.txt | tail -20; echo "mutmut did not finish" >&2; exit 1; }
tr '\r' '\n' < /tmp/mutmut-run.txt | grep -E "^[^ ]* *[0-9]+/[0-9]+ " | tail -1 || true
not_killed="$(mutmut results 2>&1 | tr '\r' '\n' | sed 's/\x1b\[[0-9;]*m//g' | grep -E ': (survived|no tests|timeout|suspicious|segfault|check was interrupted by user)$' || true)"
if [ -n "$not_killed" ]; then
  echo "$not_killed"
  echo "FAIL: the mutants above were not killed (inspect one with: cd smo/shared && PYTHONPATH=.:../r1-termination mutmut show <name>)" >&2
  exit 1
fi
echo "OK: every mutant was killed"
