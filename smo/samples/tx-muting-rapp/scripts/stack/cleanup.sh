#!/usr/bin/env bash
# Reset the sample so the demo can run again from step 00.
#
#   scripts/stack/cleanup.sh           # the rApp's data jobs and state, the simulator's config / alarms / faults
#   scripts/stack/cleanup.sh --purge   # the whole stack: containers AND volumes (the database), after confirmation
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

if [ "${1:-}" = "--purge" ]; then
  echo "This removes every container and volume of the SMO stack, including the database (not just this sample's data)."
  read -r -p "Type 'purge' to continue: " answer
  [ "$answer" = purge ] || { echo "aborted"; exit 1; }
  compose down -v --remove-orphans
  exit 0
fi

in_stack "
import os, httpx
httpx.delete('http://dme:8000/data-jobs', params={'consumer_id': '$SAMPLE_NAME'}, timeout=30).raise_for_status()
httpx.delete('http://$SAMPLE_NAME:8000/state', timeout=30).raise_for_status()
httpx.delete('http://gnb-o1-adaptor-sim:8000/control/state', timeout=30).raise_for_status()
state = os.environ.get('GNB_DEMO_STATE', '/tmp/gnb-demo.json')
if os.path.exists(state):
    os.remove(state)
print('  data jobs ended, rApp and simulator state reset, demo state removed')
"
echo "The managed element, PM subscriptions, DME actions and alarms stay as audit history; step 00 reuses the element."
