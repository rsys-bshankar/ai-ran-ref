#!/usr/bin/env bash
# Build and start the sample (rApp + O1 adaptor simulator) with the SMO services it needs.
#
#   scripts/start.sh               # the sample's services only
#   FULL_STACK=1 scripts/start.sh  # every SMO service (GUI included)
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
  compose up -d --build "${SMO_SERVICES[@]}" "${SAMPLE_SERVICES[@]}"
fi

echo "waiting for health"
wait_healthy postgres "${SMO_SERVICES[@]}" "${SAMPLE_SERVICES[@]}"
copy_demo

echo "ready: scripts/run_demo.sh (steps 00-06), scripts/cli.sh (O1 adaptor CLI)"
