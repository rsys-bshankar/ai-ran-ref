#!/usr/bin/env bash
# Run pilot steps inside the compose network. The pilot is re-copied first, so local edits take effect.
#
#   scripts/run_demo.sh           # all steps 00-08
#   scripts/run_demo.sh 02 03     # chosen steps (ids persist between runs)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

sync_pilot
compose exec -T r1-termination python3 "$SCRATCH/pilot.py" "${@:-all}"
