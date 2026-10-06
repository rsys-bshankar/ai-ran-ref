#!/usr/bin/env bash
# Run demo steps inside the compose network. demo.py is re-copied first, so local edits take effect.
#
#   scripts/run_demo.sh           # all steps 00-08
#   scripts/run_demo.sh 02 03     # chosen steps (ids persist between runs)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

copy_demo
compose exec -T r1-termination python3 "$SCRATCH/demo.py" "${@:-all}"
