#!/usr/bin/env bash
# Reset the pilot so the demo can run again from step 00.
#
#   scripts/cleanup.sh           # pilot only: its data jobs, its state file, the mock O1 Adaptor's running config
#   scripts/cleanup.sh --purge   # the whole stack: containers AND volumes (the database), after confirmation
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [ "${1:-}" = "--purge" ]; then
  echo "This removes every container and volume of the SMO stack, including the database (not just the pilot's data)."
  read -r -p "Type 'purge' to continue: " answer
  [ "$answer" = purge ] || { echo "aborted"; exit 1; }
  compose down -v --remove-orphans
  exit 0
fi

in_stack "
import os, httpx
httpx.delete('http://dme:8000/data-jobs', params={'consumer_id': '$PILOT_NAME'}, timeout=30).raise_for_status()
httpx.delete('http://mock-o1-adaptor:8000/state', timeout=30).raise_for_status()
state = os.environ.get('PILOT_STATE', '/tmp/$PILOT_NAME.json')
if os.path.exists(state):
    os.remove(state)
print('  pilot data jobs terminated, mock O1 Adaptor state reset, pilot state removed')
"
echo "The managed element, PM subscriptions, DME actions and alarms stay as audit history; step 00 reuses the element."
