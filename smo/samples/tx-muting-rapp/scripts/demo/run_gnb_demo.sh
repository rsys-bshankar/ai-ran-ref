#!/usr/bin/env bash
# Run demo steps inside the compose network. gnb_demo.py is re-copied first, so local edits take effect.
#
#   scripts/demo/run_gnb_demo.sh           # all steps 00-09
#   scripts/demo/run_gnb_demo.sh 02 03     # chosen steps (ids persist between runs)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

copy_demo
serve_package    # step 00 onboards the CSAR from here
compose exec -T r1-termination python3 "$SCRATCH/gnb_demo.py" "${@:-all}"
