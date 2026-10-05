# Shared by the pilot's helper scripts: paths, the services the pilot needs, and copying the pilot into the stack.
# shellcheck shell=bash

PILOT_NAME=ccdu-tx-muting-pilot
SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PILOT_DIR="$(dirname "$SCRIPTS_DIR")"
SMO_DIR="$(cd "$PILOT_DIR/../.." && pwd)"
REPO_DIR="$(cd "$SMO_DIR/.." && pwd)"
SCRATCH="/srv/scratch/$PILOT_NAME"

# The pilot only touches these (plus postgres and the one-shot migrate, pulled in as dependencies).
# FULL_STACK=1 starts every service instead, e.g. to open the GUI beside the pilot.
PILOT_SERVICES=(r1-termination sme dme ran-nf-oam ran-nf-oam-worker mock-o1-adaptor)

compose() { (cd "$SMO_DIR" && docker compose "$@"); }

# wait_healthy SERVICE...: until every service's container reports healthy (5 minutes at most)
wait_healthy() {
  local deadline=$((SECONDS + 300)) svc id status
  for svc in "$@"; do
    while :; do
      id="$(compose ps -q "$svc")"
      status="$( [ -n "$id" ] && docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" || echo missing)"
      [ "$status" = healthy ] && break
      if [ "$SECONDS" -ge "$deadline" ]; then
        echo "timed out waiting for $svc (status: $status); see: docker compose logs $svc" >&2
        return 1
      fi
      sleep 3
    done
    echo "  $svc: healthy"
  done
}

# sync_pilot: copy this directory into the r1-termination volume, where pilot.py runs (DEMO_RUNBOOK.md §1)
sync_pilot() {
  compose exec -T r1-termination python3 -c "import shutil; shutil.rmtree('$SCRATCH', ignore_errors=True)"
  compose cp "$PILOT_DIR" "r1-termination:$SCRATCH" >/dev/null
  echo "  pilot copied to r1-termination:$SCRATCH"
}

# in_stack PYTHON: run a Python snippet inside the compose network
in_stack() { compose exec -T r1-termination python3 -c "$1"; }
