#!/usr/bin/env bash
# Type check (PR-V-2b) of each module that is clean: `scripts/run_mypy.sh [module...]` from smo/.
# The same settings everywhere (check_untyped_defs: the bodies of functions without annotations are checked too, which is where the findings are;
# a package without type information is Any). A module joins MODULES when it has no finding; the others are listed in NOT_YET with their count,
# so the list shrinks and nothing is added to it.
set -euo pipefail
cd "$(dirname "$0")/.."
MODULES=(onboarding rapp-mgmt mlmr mllf so-smos sme dme r1-termination nfo ran-analytics mdaf sa-smos mock-o1-adaptor focom gui-bff)
# NOT_YET (findings): ran-nf-oam 68, aimgf 18, intent-service 15, sdk 97
[ "$#" -gt 0 ] && MODULES=("$@")
status=0
for m in "${MODULES[@]}"; do
  echo "== $m"
  (cd "$m" && PYTHONPATH=.:../shared python -m mypy --python-version 3.11 --check-untyped-defs --ignore-missing-imports --disable-error-code import-untyped --warn-unused-ignores --warn-redundant-casts \
      --explicit-package-bases app) || status=1
done
exit $status
