#!/usr/bin/env bash
# Package and instance lifecycle through Onboarding and rApp Management (README.md section 10).
#
#   scripts/lcm/lcm.sh up                # onboard the CSAR, prime, create the instance, complete bootstrap
#   scripts/lcm/lcm.sh status
#   scripts/lcm/lcm.sh down              # terminate, deprime, delete
#   scripts/lcm/lcm.sh onboard | prime | deploy | bootstrap | terminate | deprime | delete   # single steps
#
# Needs the platform services behind it (onboarding, rapp-mgmt, nfo, focom): scripts/stack/start.sh starts them.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

if [ "${1:-}" = services ]; then
  compose up -d "${LCM_SERVICES[@]}"
  wait_healthy "${LCM_SERVICES[@]}"
  exit 0
fi

copy_demo
serve_package
compose exec -T -e "LCM_CSAR_URL=http://r1-termination:8899/tx-muting-rapp.csar" r1-termination python3 "$SCRATCH/scripts/lcm/lcm.py" "$@"
