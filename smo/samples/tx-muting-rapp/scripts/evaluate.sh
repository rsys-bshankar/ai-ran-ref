#!/usr/bin/env bash
# Run one closed-loop pass of the rApp (POST /evaluate) and print the decision. Use it with scripts/cli.sh to drive the
# demo by hand: set the counters with `cli.sh pm <prb%> <ue>`, then evaluate.
#
#   scripts/evaluate.sh          # one pass, summary
#   scripts/evaluate.sh --json   # the full decision record
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

compose exec -T -e "FORMAT=${1:-summary}" r1-termination python3 -c "
import json, os, httpx
r = httpx.post('http://$SAMPLE_NAME:8000/evaluate', timeout=120)
if r.status_code != 200:
    raise SystemExit(f'{r.status_code}: {r.text}')
d = r.json()
if os.environ['FORMAT'] == '--json':
    print(json.dumps(d, indent=2))
else:
    v = d.get('verification') or {}
    print(f\"{d['decisionId']}  {d['currentState']} -> {d['decision']} ({d['reason']})  \"
          f\"values={ {k: x for k, x in d['instantaneousValues'].items() if k in ('dlPrbUtilization', 'rrcConnectedUeCount')} }\")
    if d['changes']:
        print(f\"  wrote {d['changes']}  read-back={v.get('result')}  attempts={d.get('attempts')}\")
    if 'rollback' in d:
        print(f\"  rolled back to full TX: {d['rollback']['verification']['result']}\")
"
