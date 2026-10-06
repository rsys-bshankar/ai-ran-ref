#!/usr/bin/env bash
# Type check (PR-V-2b..2j) of every module: `scripts/run_mypy.sh [module...]` from smo/ (the `lint` job runs it with no argument).
# The same settings everywhere (check_untyped_defs: the bodies of functions without annotations are checked too, which is where the findings are;
# a package without type information is Any). A new module is added to MODULES in the PR that adds it. Not covered: the sample rApps and tests_integration.
set -euo pipefail
cd "$(dirname "$0")/.."
MODULES=(onboarding rapp-mgmt mlmr mllf so-smos sme dme r1-termination nfo ran-analytics mdaf sa-smos mock-o1-adaptor focom gui-bff aimgf intent-service ran-nf-oam sdk)
[ "$#" -gt 0 ] && MODULES=("$@")
status=0
for m in "${MODULES[@]}"; do
  echo "== $m"
  target=app; [ -d "$m/app" ] || target=.      # the SDK is a package at the module root, tests included
  (cd "$m" && PYTHONPATH=.:../shared python -m mypy --python-version 3.11 --check-untyped-defs --ignore-missing-imports --disable-error-code import-untyped --warn-unused-ignores --warn-redundant-casts \
      --explicit-package-bases "$target") || status=1
done
exit $status
