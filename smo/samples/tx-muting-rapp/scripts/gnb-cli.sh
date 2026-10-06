#!/usr/bin/env bash
# The gNB O1 adaptor simulator's CLI. Interactive shell without arguments; asynchronous events print as they happen.
#
#   scripts/gnb-cli.sh                      # shell
#   scripts/gnb-cli.sh pm 18.4 4            # one command
#   scripts/gnb-cli.sh alarm raise 13325
#   scripts/gnb-cli.sh watch                # follow the event log
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [ $# -eq 0 ] && [ -t 0 ]; then
  compose exec gnb-o1-adaptor-sim python -m app.gnb_cli
else
  compose exec -T gnb-o1-adaptor-sim python -m app.gnb_cli "$@"
fi
