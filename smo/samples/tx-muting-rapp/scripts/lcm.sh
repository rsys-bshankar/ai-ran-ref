#!/usr/bin/env bash
# Package and instance lifecycle through Onboarding and rApp Management (README.md section 10).
#
#   scripts/lcm.sh up                # onboard the CSAR, prime, create the instance, complete bootstrap
#   scripts/lcm.sh status
#   scripts/lcm.sh down              # terminate, deprime, delete
#   scripts/lcm.sh onboard | prime | deploy | bootstrap | terminate | deprime | delete   # single steps
#
# Needs the platform services behind it: START_LCM=1 scripts/start.sh, or scripts/lcm.sh services.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [ "${1:-}" = services ]; then
  compose up -d "${LCM_SERVICES[@]}"
  wait_healthy "${LCM_SERVICES[@]}"
  exit 0
fi

# the package is built by the shared builder, next to the other samples' .csar files
python3 "$SMO_DIR/samples/build_csar.py" tx-muting-rapp >/dev/null
copy_demo
compose cp "$SMO_DIR/samples/tx-muting-rapp.csar" "r1-termination:$SCRATCH/tx-muting-rapp.csar" >/dev/null
# serve $SCRATCH (the sample and its CSAR) on :8899, unless something already answers there
in_stack "
import urllib.request, sys
try:
    urllib.request.urlopen('http://localhost:8899/tx-muting-rapp.csar', timeout=2); sys.exit(0)
except Exception:
    sys.exit(1)" || compose exec -d r1-termination python3 -m http.server 8899 --directory "$SCRATCH" >/dev/null
sleep 1
compose exec -T -e "LCM_CSAR_URL=http://r1-termination:8899/tx-muting-rapp.csar" r1-termination python3 "$SCRATCH/scripts/lcm.py" "$@"
