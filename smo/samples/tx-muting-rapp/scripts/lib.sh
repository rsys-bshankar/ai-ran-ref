# Shared by the sample's helper scripts: paths, the services it needs, the compose wrapper, copying gnb_demo.py in.
# shellcheck shell=bash

SAMPLE_NAME=tx-muting-rapp
SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SAMPLE_DIR="$(dirname "$SCRIPTS_DIR")"
SMO_DIR="$(cd "$SAMPLE_DIR/../.." && pwd)"
REPO_DIR="$(cd "$SMO_DIR/.." && pwd)"
SCRATCH="/srv/scratch/$SAMPLE_NAME"

# The rApp, its gNB O1 adaptor simulator, and what they call. postgres and the one-shot migrate come in as dependencies.
# FULL_STACK=1 starts every service instead, e.g. to open the GUI beside the sample.
SAMPLE_SERVICES=(tx-muting-rapp gnb-o1-adaptor-sim)
# what the package and instance lifecycle needs: the demo deploys the rApp from its CSAR (README section 9.3)
LCM_SERVICES=(onboarding rapp-mgmt nfo focom)
SMO_SERVICES=(r1-termination sme dme ran-nf-oam ran-nf-oam-worker "${LCM_SERVICES[@]}")

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

# copy_demo: put gnb_demo.py into the r1-termination volume, where it runs (DEMO_RUNBOOK.md section 1)
copy_demo() {
  # `docker cp` creates root-owned files, which the service user cannot remove: clear as root, or an older copy stays
  compose exec -T -u 0 r1-termination python3 -c "import shutil; shutil.rmtree('$SCRATCH', ignore_errors=True)"
  compose cp "$SAMPLE_DIR/." "r1-termination:$SCRATCH" >/dev/null
  echo "  demo copied to r1-termination:$SCRATCH"
}

# in_stack PYTHON: run a Python snippet inside the compose network
in_stack() { compose exec -T r1-termination python3 -c "$1"; }

# serve_package: build the CSAR and serve it on :8899 inside the compose network, where Onboarding fetches it.
# Run after copy_demo, which clears the scratch directory. The server is left running once started.
serve_package() {
  python3 "$SMO_DIR/samples/build_csar.py" "$SAMPLE_NAME" >/dev/null
  compose cp "$SMO_DIR/samples/$SAMPLE_NAME.csar" "r1-termination:$SCRATCH/$SAMPLE_NAME.csar" >/dev/null
  in_stack "
import urllib.request, sys
try:
    urllib.request.urlopen('http://localhost:8899/$SAMPLE_NAME.csar', timeout=2); sys.exit(0)
except Exception:
    sys.exit(1)" || compose exec -d r1-termination python3 -m http.server 8899 --directory "$SCRATCH" >/dev/null
  sleep 1
  echo "  package served: http://r1-termination:8899/$SAMPLE_NAME.csar"
}
