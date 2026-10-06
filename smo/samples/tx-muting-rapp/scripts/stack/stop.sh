#!/usr/bin/env bash
# Stop the stack. Containers, the database and every volume are kept; scripts/stack/start.sh resumes.
#
#   scripts/stack/stop.sh          # stop containers
#   scripts/stack/stop.sh --down   # also remove the containers (volumes kept)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

if [ "${1:-}" = "--down" ]; then
  compose down --remove-orphans
else
  compose stop
fi
