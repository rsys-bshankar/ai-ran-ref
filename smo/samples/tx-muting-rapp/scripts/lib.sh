# Shared by the sample's helper scripts: paths, the services it needs, the compose wrapper, copying demo.py in.
# shellcheck shell=bash

SAMPLE_NAME=tx-muting-rapp
SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SAMPLE_DIR="$(dirname "$SCRIPTS_DIR")"
SMO_DIR="$(cd "$SAMPLE_DIR/../.." && pwd)"
REPO_DIR="$(cd "$SMO_DIR/.." && pwd)"
SCRATCH="/srv/scratch/$SAMPLE_NAME"

# The rApp, its O1 adaptor simulator, and what they call. postgres and the one-shot migrate come in as dependencies.
# FULL_STACK=1 starts every service instead, e.g. to open the GUI beside the sample.
SAMPLE_SERVICES=(tx-muting-rapp o1-adaptor-sim)
SMO_SERVICES=(r1-termination sme dme ran-nf-oam ran-nf-oam-worker)
# what the package and instance lifecycle (scripts/lcm.sh) adds
LCM_SERVICES=(onboarding rapp-mgmt nfo focom)

# the SMO stack plus this sample's overlay
compose() { (cd "$SMO_DIR" && docker compose -f "$SMO_DIR/docker-compose.yml" -f "$SAMPLE_DIR/docker-compose.yml" "$@"); }

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

# copy_demo: put demo.py into the r1-termination volume, where it runs (DEMO_RUNBOOK.md section 1)
copy_demo() {
  # `docker cp` creates root-owned files, which the service user cannot remove: clear as root, or an older copy stays
  compose exec -T -u 0 r1-termination python3 -c "import shutil; shutil.rmtree('$SCRATCH', ignore_errors=True)"
  compose cp "$SAMPLE_DIR/." "r1-termination:$SCRATCH" >/dev/null
  echo "  demo copied to r1-termination:$SCRATCH"
}

# in_stack PYTHON: run a Python snippet inside the compose network
in_stack() { compose exec -T r1-termination python3 -c "$1"; }
