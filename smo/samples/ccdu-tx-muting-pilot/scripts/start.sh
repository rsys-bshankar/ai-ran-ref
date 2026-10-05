#!/usr/bin/env bash
# Start the SMO services the CCDU TX-muting pilot needs and copy the pilot into the stack.
#
#   scripts/start.sh              # the pilot's services only
#   FULL_STACK=1 scripts/start.sh # every service (GUI included)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }

if [ ! -s "$SMO_DIR/secrets/db_password" ]; then
  echo "creating secrets (once)"
  (cd "$SMO_DIR" && scripts/init_secrets.sh)
fi

echo "starting the stack"
if [ "${FULL_STACK:-0}" = 1 ]; then
  compose up -d --build
else
  compose up -d --build "${PILOT_SERVICES[@]}"
fi

echo "waiting for health"
wait_healthy postgres "${PILOT_SERVICES[@]}"
sync_pilot

echo "ready: run scripts/run_demo.sh (all steps) or scripts/run_demo.sh 00 01 ..."
