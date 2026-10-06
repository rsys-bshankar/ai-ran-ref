#!/usr/bin/env bash
# TX-muting rApp: the guided end-to-end demo. This is the only script you need.
#
#   ./start.sh            interactive: builds and starts the stack, then walks you through the CSAR lifecycle
#                         (build, onboard, prime, deploy, bootstrap), the closed loop (gNB, rApp, low load,
#                         hysteresis, restore, failure injection, audit) and the retirement, explaining each step
#                         and every event as it happens. On exit it removes everything it created.
#   ./start.sh --auto     the same, with no prompts and default load values (also used when stdin is not a terminal)
#   ./start.sh --keep     do not clean up on exit (containers, images and secrets stay; remove them with
#                         `docker compose -f ../../docker-compose.yml -f docker-compose.yml down -v --rmi local`)
#
# At each prompt: [Enter] runs the step, s skips it, q quits (and cleans up). Ctrl-C does the same.
# Needs: Docker Engine with the Compose plugin, Python 3, free host port 8080.
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1   # nothing of ours is written into the source tree

AUTO=0; KEEP=0
for arg in "$@"; do
  case "$arg" in
    --auto) AUTO=1 ;;
    --keep) KEEP=1 ;;
    -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option $arg (see ./start.sh --help)" >&2; exit 2 ;;
  esac
done
[ -t 0 ] || AUTO=1

SAMPLE_NAME=tx-muting-rapp
SAMPLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SMO_DIR="$(cd "$SAMPLE_DIR/../.." && pwd)"
SCRATCH="/srv/scratch/$SAMPLE_NAME"
RAPP=tx-muting-rapp
GNB=gnb-o1-adaptor-sim
LCM_SERVICES=(onboarding rapp-mgmt nfo focom)
PLATFORM=(r1-termination sme dme ran-nf-oam ran-nf-oam-worker "${LCM_SERVICES[@]}")
CSAR_URL="http://r1-termination:8899/$SAMPLE_NAME.csar"

WORK=""; NARRATOR_PID=""; STACK_TOUCHED=0; CLEANED=0
SECRETS_DIR_EXISTED=0; CREATED_SECRETS=()

if [ -t 1 ]; then
  B=$'\033[1m'; DIM=$'\033[2m'; CY=$'\033[36m'; GR=$'\033[32m'; YE=$'\033[33m'; RD=$'\033[31m'; N=$'\033[0m'; NARRATE_COLOR=1
else
  B=""; DIM=""; CY=""; GR=""; YE=""; RD=""; N=""; NARRATE_COLOR=0
fi

# ---------------------------------------------------------------- output helpers
title()   { printf '\n%s%s%s\n%s\n' "$B$CY" "$*" "$N" "$(printf '%*s' "${#1}" '' | tr ' ' '-')"; }
say()     { printf '%s\n' "$*"; }
ok()      { printf '%s  ok%s  %s\n' "$GR" "$N" "$*"; }
warn()    { printf '%s  !!%s  %s\n' "$YE" "$N" "$*"; }
die()     { printf '%s  error%s  %s\n' "$RD" "$N" "$*" >&2; exit 1; }
explain() { printf '%s\n' "$*" | fold -s -w 100 | sed 's/^/    /'; }
notice()  { printf '%s  what to notice:%s %s\n' "$B" "$N" "$*" | fold -s -w 104 | sed '2,$s/^/    /'; }
run_line() { printf '%s  $ %s%s\n' "$DIM" "$*" "$N"; }

COMPOSE_ARGS=(--project-directory "$SMO_DIR" -f "$SMO_DIR/docker-compose.yml" -f "$SAMPLE_DIR/docker-compose.yml")
compose() { docker compose "${COMPOSE_ARGS[@]}" "$@"; }
in_rt()   { compose exec -T r1-termination "$@" </dev/null; }   # stdin detached: a step must not eat the next prompt's keystrokes

# ---------------------------------------------------------------- prompts
ask() {   # [Enter] run, s skip, q quit. Returns 0 to run, 1 to skip.
  [ "$AUTO" = 1 ] && return 0
  local a
  while :; do
    read -r -p "${B}  > [Enter] run   [s] skip   [q] quit and clean up: ${N}" a || { echo; exit 0; }
    case "$a" in ""|r|R|y|Y) return 0 ;; s|S) return 1 ;; q|Q) exit 0 ;; esac
  done
}
pause() { [ "$AUTO" = 1 ] && return 0; local a; read -r -p "${B}  > [Enter] to continue, q to quit: ${N}" a || exit 0; [ "$a" = q ] && exit 0; return 0; }
ask_number() {   # prompt, default -> prints the value
  local v
  if [ "$AUTO" = 1 ]; then echo "$2"; return; fi
  while :; do
    read -r -p "  $1 [$2]: " v || exit 0
    v="${v:-$2}"
    [[ "$v" =~ ^[0-9]+(\.[0-9]+)?$ ]] && { echo "$v"; return; }
    echo "  please enter a number" >&2
  done
}
attempt() {   # run a command; on failure offer retry / continue / quit
  local a rc
  while :; do
    "$@"; rc=$?
    [ "$rc" -eq 0 ] && return 0
    warn "that failed (exit $rc)"
    [ "$AUTO" = 1 ] && exit 1
    read -r -p "${B}  > [r] retry   [c] continue anyway   [q] quit and clean up: ${N}" a || exit 1
    case "$a" in r|R) ;; c|C) return 1 ;; q|Q) exit 1 ;; esac
  done
}

# ---------------------------------------------------------------- cleanup (runs on every exit)
cleanup() {
  [ "$CLEANED" = 1 ] && return
  CLEANED=1
  trap - EXIT INT TERM
  [ -n "$NARRATOR_PID" ] && kill "$NARRATOR_PID" 2>/dev/null
  if [ "$KEEP" = 1 ]; then
    title "Leaving everything in place (--keep)"
    say "    containers, volumes, images and any generated secrets stay; remove them with:"
    say "    docker compose -f ../../docker-compose.yml -f docker-compose.yml down -v --rmi local"
    [ -n "$WORK" ] && rm -rf "$WORK"
    return
  fi
  title "Cleaning up"
  if [ "$STACK_TOUCHED" = 1 ]; then
    if compose down -v --remove-orphans --rmi local --timeout 20 >/dev/null 2>&1; then
      ok "containers, networks, volumes (database, scratch) and the images built for the stack are removed"
    else
      warn "docker compose down reported a problem; check: docker compose -f ../../docker-compose.yml -f docker-compose.yml ps -a"
    fi
    local left; left="$(docker ps -aq --filter "label=com.docker.compose.project=$(basename "$SMO_DIR")" 2>/dev/null | wc -l)"
    [ "$left" -eq 0 ] && ok "no container of the stack is left" || warn "$left container(s) of the stack are still present"
  fi
  local f
  for f in ${CREATED_SECRETS[@]+"${CREATED_SECRETS[@]}"}; do rm -f "$SMO_DIR/secrets/$f"; done
  if [ "${#CREATED_SECRETS[@]}" -gt 0 ]; then
    [ "$SECRETS_DIR_EXISTED" = 0 ] && rmdir "$SMO_DIR/secrets" 2>/dev/null
    ok "the ${#CREATED_SECRETS[@]} secret file(s) this run generated are removed"
  fi
  if [ -n "$WORK" ]; then rm -rf "$WORK"; ok "temporary files (the built package, step output) are removed"; fi
  say "    left alone on purpose: Docker's shared build cache and the base images (other projects use them);"
  say "    prune them yourself with: docker builder prune"
}
trap cleanup EXIT
trap 'echo; warn "interrupted"; exit 130' INT TERM

# ---------------------------------------------------------------- helpers for the steps
wait_healthy() {
  local deadline=$((SECONDS + 300)) svc id status
  for svc in "$@"; do
    while :; do
      id="$(compose ps -q "$svc" 2>/dev/null)"
      status="$( [ -n "$id" ] && docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" 2>/dev/null || echo missing)"
      [ "$status" = healthy ] && break
      [ "$SECONDS" -ge "$deadline" ] && die "timed out waiting for $svc (status: $status); see: docker compose logs $svc"
      sleep 3
    done
    printf '    %-22s healthy\n' "$svc"
  done
}

copy_sample() {   # the sample (gnb_demo.py, scripts/) into the r1-termination volume, where the helpers run
  compose exec -T -u 0 r1-termination python3 -c "import shutil; shutil.rmtree('$SCRATCH', ignore_errors=True)" </dev/null
  compose cp "$SAMPLE_DIR/." "r1-termination:$SCRATCH" >/dev/null || return 1
}

build_package() {   # the CSAR, built by the shared builder, into the temporary directory (the tree is not touched)
  python3 -c "
import sys
sys.path.insert(0, '$SMO_DIR/samples')
import build_csar
open('$WORK/$SAMPLE_NAME.csar', 'wb').write(build_csar.build_bytes('$SAMPLE_NAME'))" || return 1
  python3 -c "
import zipfile
z = zipfile.ZipFile('$WORK/$SAMPLE_NAME.csar')
print('    %s: %d bytes, %d files' % ('$SAMPLE_NAME.csar', sum(i.compress_size for i in z.infolist()), len(z.namelist())))
for n in z.namelist():
    print('      ' + n)"
}

serve_package() {   # copy the CSAR beside the sample in the container and serve it on :8899, where Onboarding fetches it
  compose cp "$WORK/$SAMPLE_NAME.csar" "r1-termination:$SCRATCH/$SAMPLE_NAME.csar" >/dev/null || return 1
  in_rt python3 -c "
import urllib.request, sys
try:
    urllib.request.urlopen('http://localhost:8899/$SAMPLE_NAME.csar', timeout=2); sys.exit(0)
except Exception:
    sys.exit(1)" || compose exec -d r1-termination python3 -m http.server 8899 --directory "$SCRATCH" >/dev/null </dev/null
  sleep 1
  say "    package served inside the compose network at $CSAR_URL"
}

lcm()  { in_rt env "LCM_CSAR_URL=$CSAR_URL" python3 "$SCRATCH/scripts/lcm.py" "$@"; }
demo() { local step="$1"; shift; in_rt env "$@" python3 "$SCRATCH/gnb_demo.py" "$step"; }
demo_logged() { demo "$@" | tee "$WORK/last.out"; return "${PIPESTATUS[0]}"; }
gnb_cli() { compose exec -T "$GNB" python -m app.gnb_cli "$@" </dev/null; }

rapp_up() {   # NFO has no container runtime, so the host starts the workload once the instance is RUNNING
  compose up -d --build "$RAPP" >/dev/null 2>&1 || return 1
  wait_healthy "$RAPP"
}
rapp_down() { compose rm -f -s "$RAPP" >/dev/null 2>&1; say "    $RAPP: stopped and removed"; }

r1_trace() {   # the R1 Termination log lines that carry a decision id: proof the rApp's calls went through R1
  local id; id="$(grep -o 'TXM-[0-9]*' "$WORK/last.out" 2>/dev/null | head -1)"
  [ -n "$id" ] || return 0
  say "  R1 Termination log lines carrying correlation id $id (the rApp's calls, with its SME token):"
  compose logs --no-log-prefix r1-termination 2>/dev/null | grep "\"correlationId\": \"$id\"" \
    | sed -E 's/.*"message": "(.*)", "correlationId".*/\1/; s/\\"/"/g; s/^/      /' | sort -u | head -12
}

thresholds_line() {
  python3 -c "
import json
c = json.load(open('$SAMPLE_DIR/app/thresholds.json'))
a, d = c['activation'], c['deactivation']
print('    mute when PRB < %s %% and UEs < %s; restore when PRB >= %s %% or UEs >= %s; in between: do nothing'
      % (a['prbUtilizationPercent'], a['rrcConnectedUeCount'], d['prbUtilizationPercent'], d['rrcConnectedUeCount']))"
}

# ================================================================ the guided session
title "TX-muting rApp: guided end-to-end demo"
explain "You will deploy an rApp from its CSAR package through the SMO's lifecycle services, then watch it run a closed loop on a simulated gNB, then retire it. Every step is explained first and you decide when it runs. Events from both the rApp and the gNB are explained live as they happen, in colour: cyan for the rApp, magenta for the gNB."
cat <<EOF

      +--------+   PM / alarms    +-------------+    +-----+      +-----------+  R1 + SME token  +----------------+
      |  gNB   |----------------->| RAN NF OAM  |--->| DME |----->|  rApp     |----------------->| R1 Termination |
      | (sim)  |<-----------------|  (O1)       |<---+     |<-----+ (decides  |  (reads PM,      +----------------+
      +--------+  O1 edit-config  +-------------+  /actions      |  by itself)|   writes config)
                                                                  +-----------+
      Lifecycle:  CSAR --> Onboarding --> rApp Management --> NFO / FOCOM      (the rApp container is started by this script)
EOF

# ---- checks
title "0. Checking your machine"
command -v docker >/dev/null 2>&1 || die "docker is required"
docker info >/dev/null 2>&1 || die "the Docker daemon is not reachable"
docker compose version >/dev/null 2>&1 || die "the Docker Compose plugin is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
python3 -c "
import socket, sys
s = socket.socket()
sys.exit(1 if s.connect_ex(('127.0.0.1', 8080)) else 0)" && die "host port 8080 (R1 Termination) is already in use"
ok "docker, compose, python3 found; port 8080 is free"
existing="$(compose ps -aq 2>/dev/null | wc -l)"
if [ "$existing" -gt 0 ]; then
  warn "$existing container(s) of the SMO stack already exist. This script removes the whole stack, volumes included, when it exits."
  [ "$AUTO" = 1 ] && die "refusing to continue unattended; remove it first: docker compose -f ../../docker-compose.yml -f docker-compose.yml down -v"
  read -r -p "  Continue and let this script own (and later delete) that stack? [y/N] " a || exit 0
  [ "$a" = y ] || [ "$a" = Y ] || exit 0
fi
WORK="$(mktemp -d)"
[ -d "$SMO_DIR/secrets" ] && SECRETS_DIR_EXISTED=1
ls "$SMO_DIR/secrets" 2>/dev/null | sort > "$WORK/secrets.before"

# ---- stack
title "1. Starting the platform"
explain "Builds and starts the SMO services the demo needs (R1 Termination, SME, DME, RAN NF OAM and its worker, plus Onboarding, rApp Management, NFO and FOCOM for the package lifecycle) and the gNB O1 adaptor simulator. The first run builds the images, which takes a few minutes. The rApp is built but not started: it is the workload of the CSAR deployment and starts in step L5."
if ask; then
  say "    making sure the secret files exist (the ones this run creates are removed again on exit; existing ones are kept)"
  (cd "$SMO_DIR" && scripts/init_secrets.sh >/dev/null) || die "could not create the secrets"
  ls "$SMO_DIR/secrets" 2>/dev/null | sort > "$WORK/secrets.after"
  mapfile -t CREATED_SECRETS < <(comm -13 "$WORK/secrets.before" "$WORK/secrets.after")
  STACK_TOUCHED=1
  run_line "docker compose up -d --build <platform services> $GNB"
  attempt compose up -d --build "${PLATFORM[@]}" "$GNB" || exit 1
  attempt compose build "$RAPP" >/dev/null || exit 1
  say "  waiting for every service to report healthy:"
  wait_healthy postgres "${PLATFORM[@]}" "$GNB"
  attempt copy_sample || exit 1
  ok "stack is up; the demo helpers are copied into r1-termination"
  explain "From here on a narrator prints every event of the rApp and the gNB with a short explanation. Lines you do not type yourself belong to it."
  docker compose "${COMPOSE_ARGS[@]}" exec -T -e "NARRATE_COLOR=$NARRATE_COLOR" r1-termination python3 -u "$SCRATCH/scripts/narrate.py" &
  NARRATOR_PID=$!   # the docker process itself, not a subshell, so cleanup can stop it
else
  die "the stack is needed for everything that follows"
fi

# ---------------------------------------------------------------- CSAR lifecycle
title "L1. Build the CSAR package"
explain "A CSAR is a zip: the Application Service Descriptor (Definitions/asd.yaml, TOSCA), manifest.yaml and capabilities.yaml (what the rApp needs and provides) and the rApp's source. The shared builder, smo/samples/build_csar.py, produces it with fixed timestamps so the same sources give the same bytes. Here it is built into a temporary folder and discarded at exit; the committed package in the repository is not touched."
if ask; then
  run_line "python3 smo/samples/build_csar.py $SAMPLE_NAME  (to a temporary file)"
  attempt build_package || exit 1
  notice "no tests, no README, no simulator and no scripts are in the package: only what the platform needs to know about the rApp."
fi

title "L2. Onboard the package (Onboarding)"
explain "Onboarding fetches the CSAR over HTTP, validates it (TOSCA.meta, the ASD), stores the package and its capabilities, and asks NFO to create a deployment descriptor for it. The package becomes AVAILABLE. Nothing runs yet. The script first serves the CSAR inside the compose network, as the operator or a pipeline would publish it."
if ask; then
  attempt serve_package || exit 1
  run_line "POST onboarding/packages {location: $CSAR_URL}, then poll onboarding-status"
  attempt lcm onboard
  notice "state AVAILABLE and a nfDeploymentDescriptorId (NFO now knows the package). The capabilities come from capabilities.yaml and manifest.yaml: this rApp consumes 'data' and 'platform', runs in INFERENCE mode, autonomy AUTONOMOUS, and needs DME and RAN-NF-OAM. Onboarding refuses a byte-identical second copy, so you cannot onboard the same CSAR twice without retiring the first."
fi

title "L3. Prime the package"
explain "Priming prepares an onboarded package for deployment (in the O-RAN rApp lifecycle, AVAILABLE becomes PRIMED). In this build there is no ACM, DME or SME pre-provisioning behind it, so it is a pure state change. It also arms a guard: a primed package cannot be deprimed while an instance of it exists."
if ask; then
  run_line "POST onboarding/packages/{id}/prime"
  attempt lcm prime
  notice "state PRIMED."
fi

title "L4. Create the instance (rApp Management -> NFO)"
explain "CreateInstance asks rApp Management to deploy the package. It checks the package, asks NFO to instantiate a deployment (placement comes from FOCOM's inventory), and records the instance as DEPLOYING with its own OAuth client id. This demo asks for autonomy mode AUTONOMOUS: the rApp writes to the network without a human approving each change."
if ask; then
  run_line "POST rapp-mgmt/instances {packageId, autonomyMode: AUTONOMOUS}"
  attempt lcm deploy
  notice "an instanceId and the oauthClientId, and state DEPLOYING. NFO in this build has no container runtime (no Helm, no 'docker run'), so no container exists yet. That is why the next step starts it."
fi

title "L5. Complete the bootstrap and start the rApp container"
explain "In a real deployment the rApp container calls rApp Management's bootstrap-complete when it is up, and the instance becomes RUNNING. Here the script sends that call, and then starts the rApp container itself with docker compose, standing in for the deployment manager. The rApp's first call through R1 Termination makes it register at SME as an API invoker (an rApp: no enrollment secret) and fetch a client-credentials token; every call after that carries the token."
if ask; then
  run_line "POST rapp-mgmt/instances/{id}/bootstrap-complete"
  attempt lcm bootstrap
  run_line "docker compose up -d --build $RAPP"
  attempt rapp_up || exit 1
  compose ps --format 'table {{.Service}}\t{{.Status}}' "$RAPP" "$GNB" 2>/dev/null | sed 's/^/    /'
  notice "the instance is RUNNING and the rApp container is healthy. The rApp is idle: it has no target yet and decides nothing."
fi

# ---------------------------------------------------------------- the closed loop
title "G1. Prepare the gNB (the simulated network side)"
explain "The gNB O1 adaptor simulator registers itself with RAN NF OAM (the SMO's O1 consumer), sends a heartbeat so the endpoint becomes ACTIVE (RAN NF OAM only writes to ACTIVE endpoints), and subscribes to the two PM counters the rApp uses: DL_PRB_UTILIZATION and RRC_CONNECTED_UE. Subscribing creates the DME data types. An initial config job then writes the cell's starting TX-muting configuration: feature enabled, HORIZONTAL_PLANE, MUTING_OFF."
if ask; then
  attempt demo_logged 00
  notice "'endpoint.registered' from the gNB, 'initial config job: COMPLETED', and the cell reading MUTING_OFF with the feature enabled. The first edit-config arrives at the gNB as 'config.received'."
fi

title "G2. Start the rApp"
explain "POST /start gives the rApp its target (managed element and cell), checks its thresholds file and opens one DME data job per counter. From this moment the rApp evaluates by itself, one pass every few seconds: read the latest PM from DME and the cell's configuration from RAN NF OAM, decide, and if needed write through DME's /actions and read the result back."
thresholds_line
if ask; then
  attempt demo_logged 01
  notice "events 'started' and 'auto-evaluation.started'. The next events appear only when something changes: repeated 'no change' passes are not shown."
fi

title "G3. Low load: the rApp mutes the cell"
explain "The gNB reports a low load; the rApp's next pass should mute half of the TX paths to save energy. You choose the numbers. Muting needs PRB below the activation threshold and fewer UEs than its limit, the feature enabled, and the cell currently not muted."
thresholds_line
if ask; then
  prb="$(ask_number "PRB utilisation in % (below the mute threshold)" 18.4)"
  ue="$(ask_number "RRC connected UEs (below the limit)" 4)"
  say "  the gNB reports it, and the script waits for the rApp's own next pass (at most a few seconds):"
  attempt demo_logged 02 "GNB_LOW_PRB=$prb" "GNB_LOW_UE=${ue%.*}"
  r1_trace
  notice "the gNB's 'pm.reported', then the rApp's 'decision' (REDUCED_TX) with its reason, the gNB's 'config.received' (the O1 write that came down from the rApp through DME and RAN NF OAM), 'tx-state.changed MUTING_OFF -> MUTING_ON', and 'read-back VERIFIED': the rApp read the configuration back and the cell really has it. If you chose values above a threshold, the decision is NO_CHANGE and the reason says which limit blocked it."
fi

title "G4. What exactly was written, and who did it"
explain "Shows the DME action (requestedBy tx-muting-rapp, its correlationId equal to the decision id), the RAN NF OAM config job it became, and what the gNB received and now runs. The decision id travels as the correlation id through every hop, so one id ties the rApp's decision to the O1 write."
if ask; then
  attempt demo_logged 03
fi

title "G5. Load inside the hysteresis band: nothing happens"
explain "The two thresholds differ on purpose. Once the cell is muted, the rApp restores full TX only when load reaches the higher (deactivation) threshold, so a load that wobbles between the two does not make the cell flip back and forth."
thresholds_line
if ask; then
  prb="$(ask_number "PRB in % (between the two thresholds)" 41)"
  ue="$(ask_number "RRC connected UEs" 8)"
  attempt demo_logged 04 "GNB_MID_PRB=$prb" "GNB_MID_UE=${ue%.*}"
  notice "decision NO_CHANGE with reason LOAD_WITHIN_HYSTERESIS and no write. This pass is not an event, because it repeats the previous state of affairs; the decision is in the log above and in the audit (G7)."
fi

title "G6. High load: the rApp restores full TX"
explain "When PRB reaches the deactivation threshold, or the UE count does, the rApp returns the cell to full TX. Restoring is the safe direction, so it is never rolled back."
if ask; then
  prb="$(ask_number "PRB in % (at or above the restore threshold)" 45)"
  ue="$(ask_number "RRC connected UEs" 8)"
  attempt demo_logged 05 "GNB_HIGH_PRB=$prb" "GNB_HIGH_UE=${ue%.*}"
  r1_trace
  notice "decision FULL_TX (PRB_HIGH or UE_COUNT_HIGH), 'config.received' with MUTING_OFF, 'tx-state.changed MUTING_ON -> MUTING_OFF', read-back VERIFIED."
fi

title "G7. Break the write path on purpose (optional)"
explain "Two experiments with a gNB that lies. First one edit-config is acknowledged but not applied: the rApp's read-back disagrees, it retries once, and the retry works (attempts=2). Then two in a row are ignored: the write and its retry both fail, so the rApp rolls the cell back to full TX and says so. The rApp's loop keeps running, so on its next pass, with the faults used up, it mutes the cell for real."
if ask; then
  run_line "gnb-cli fault IGNORE_WRITE 1  (the next edit-config is acknowledged but not applied)"
  attempt gnb_cli fault IGNORE_WRITE 1 >/dev/null
  attempt demo_logged 02 "GNB_LOW_PRB=18" "GNB_LOW_UE=4"
  notice "'config.fault' from the gNB, then the rApp's decision REDUCED_TX with 'read-back VERIFIED after 2 attempt(s)'."
  attempt demo_logged 05 >/dev/null
  run_line "gnb-cli fault IGNORE_WRITE 2"
  attempt gnb_cli fault IGNORE_WRITE 2 >/dev/null
  attempt demo_logged 02 "GNB_LOW_PRB=18" "GNB_LOW_UE=4"
  notice "two 'config.fault' lines, then a decision with read-back VERIFY_FAILED after 2 attempts and 'the rApp rolled the cell back to full TX'. A moment later, with no faults left, its next pass mutes the cell successfully (a second decision)."
  pause
  attempt demo_logged 05 >/dev/null
fi

title "G8. Audit"
explain "Every pass of the rApp's loop is recorded with the evidence it used. Repeated no-change passes are collapsed into one line. The count of DME actions is what the rApp actually asked the platform to write."
if ask; then attempt demo_logged 06; fi

if [ "$AUTO" = 0 ]; then
  title "G9. Play with the gNB yourself (optional)"
  explain "Opens the gNB simulator's CLI. Try: pm 18 4 (low load), pm 45 8 (high load), counter DL_PRB_UTILIZATION 30, gen start 5 (random load every 5 s, watch the rApp follow it), alarm raise 13325, config show, events 20, help. The narrator keeps explaining what happens. Type quit to come back."
  if ask; then compose exec "$GNB" python -m app.gnb_cli; fi
fi

# ---------------------------------------------------------------- retirement
title "R1. Terminate the instance"
explain "Terminate asks rApp Management to retire the instance: NFO removes the deployment, and rApp Management tells Onboarding the package is no longer in use. The instance becomes UNDEPLOYED. Until then the package could not be deprimed."
if ask; then
  attempt lcm terminate
  notice "state UNDEPLOYED. The rApp container is still running: stopping the workload is the deployment manager's job, and here it is the next step."
fi

title "R2. Stop the rApp container"
explain "The host stands in for the deployment manager again and removes the container that the instance stood for. The rApp's decision loop stops with it. Its state, kept in memory, is gone."
if ask; then run_line "docker compose rm -f -s $RAPP"; rapp_down; fi

title "R3. Deprime, delete the instance, retire the package"
explain "Deprime (PRIMED back to AVAILABLE) is allowed now that no instance uses the package; try it earlier and the platform answers 409. Then the instance record is deleted, and the package goes AVAILABLE, DEPRECATED, DELETING, so the same CSAR could be onboarded again."
if ask; then
  attempt lcm deprime
  attempt lcm delete
  attempt lcm retire
  notice "package DELETING, instance deleted. Everything the CSAR created on the platform is gone again."
fi

title "Done"
explain "The demo is finished. Pressing Enter removes everything this script created: every container, network and volume of the stack, the images it built, the generated secrets, and its temporary files."
pause
exit 0
