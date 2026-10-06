#!/usr/bin/env bash
# The O1 adaptor simulator's CLI. Interactive shell without arguments; asynchronous events print as they happen.
#
#   scripts/cli.sh                      # shell
#   scripts/cli.sh pm 18.4 4            # one command
#   scripts/cli.sh alarm raise 13325
#   scripts/cli.sh watch                # follow the event log
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [ $# -eq 0 ] && [ -t 0 ]; then
  compose exec o1-adaptor-sim python -m app.cli
else
  compose exec -T o1-adaptor-sim python -m app.cli "$@"
fi
