#!/usr/bin/env bash
# What the pods say and do while a load runs through an upgrade (V-10), kept because a pod that is replaced takes its log with it.
#
#   scripts/k8s_diag.sh start <dir>    follow the logs of every pod of the namespace as it appears, and record the events, the pod phases and the database's lock waits
#   scripts/k8s_diag.sh stop  <dir>    end the recorder and its followers
#   scripts/k8s_diag.sh report <dir>   print, to the job log, the timeline of what was replaced and the log lines that are errors, 401s, 5xx or lock waits
#
# Times are UTC to the millisecond (the pod logs and `kubectl logs --timestamps` are too), so a failed call in load-results.json ("at") can be put next to them.
# Needs kubectl and the namespace (default smo); the database is the pod postgres-0 of the chart.
set -uo pipefail
ns="${SMO_NAMESPACE:-smo}"
cmd="${1:?start|stop|report}"
dir="${2:?directory}"

stamp() { while IFS= read -r line; do printf '%s %s\n' "$(date -u +%T.%3N)" "$line"; done; }

start() {   # detaches: the recorder runs in a session of its own and outlives the step that started it
  mkdir -p "$dir/logs"
  setsid nohup "$0" run "$dir" > "$dir/recorder.log" 2>&1 &
  sleep 2
  echo "recording the pods of namespace $ns into $dir"
}

run() {
  mkdir -p "$dir/logs"
  echo $$ > "$dir/leader"
  # events and pod phases, one line per change
  kubectl -n "$ns" get events -w -o 'custom-columns=OBJECT:.involvedObject.name,REASON:.reason,MESSAGE:.message' --no-headers 2>&1 | stamp > "$dir/events.txt" &
  kubectl -n "$ns" get pods -w --no-headers 2>&1 | stamp > "$dir/pods.txt" &
  # the database: how many sessions wait for a lock, and on what, once a second
  ( while :; do
      out=$(kubectl -n "$ns" exec postgres-0 -- psql -U smo -d smo -Atc "SELECT count(*) FILTER (WHERE wait_event_type = 'Lock'), count(*), coalesce(string_agg(DISTINCT left(query, 70), ' | ') FILTER (WHERE wait_event_type = 'Lock'), '') FROM pg_stat_activity WHERE datname = 'smo'" 2>&1 | tail -1)
      printf '%s %s\n' "$(date -u +%T.%3N)" "$out"
      sleep 1
    done > "$dir/db-locks.txt" 2>&1 ) &
  # every pod, from the moment it runs until its containers end
  ( followed=" "
    while :; do
      for p in $(kubectl -n "$ns" get pods --field-selector=status.phase=Running -o name 2>/dev/null | sed 's#^pod/##'); do
        case "$followed" in
          *" $p "*) ;;
          *) followed="$followed$p "
             kubectl -n "$ns" logs -f "$p" --all-containers --timestamps --since=1h > "$dir/logs/$p.log" 2>&1 &
             ;;
        esac
      done
      sleep 1
    done ) &
  wait
}

stop() {   # the recorder is the leader of its own process group: this ends it and everything it started
  [ -f "$dir/leader" ] || exit 0
  kill -- "-$(cat "$dir/leader")" 2>/dev/null || true
  sleep 1
}

show() {   # show <title> <file> <egrep pattern or empty> <max lines>
  echo; echo "---- $1"
  if [ -n "$3" ]; then grep -E "$3" "$2" 2>/dev/null | cut -c1-300 | head -"$4"; else cut -c1-300 "$2" 2>/dev/null | head -"$4"; fi
}

report() {
  echo "==== what happened to the pods while the load ran (UTC) ===="
  show "events: pods stopped, created, started, failing a probe, scaled" "$dir/events.txt" ' Killing | Created | Started | Unhealthy | BackOff |ScalingReplicaSet|Evicted|OOM|FailedScheduling' 160
  show "pod phases" "$dir/pods.txt" '' 120
  echo; echo "---- database sessions waiting for a lock (time, waiting, all, the queries that wait): only the seconds with a waiter"
  awk -F'[ |]' '$2 + 0 > 0' "$dir/db-locks.txt" 2>/dev/null | cut -c1-300 | head -60
  echo "(seconds sampled: $(wc -l < "$dir/db-locks.txt" 2>/dev/null || echo 0))"
  echo; echo "---- log lines that are errors, warnings, or an answer of 401 or 5xx, per pod (the first 25 of each)"
  for f in "$dir"/logs/*.log; do
    [ -e "$f" ] || continue
    n=$(grep -cE '"level": "(ERROR|WARNING|CRITICAL)"|"status": (401|50[0-9])|Traceback|Error' "$f" 2>/dev/null || true)
    [ "${n:-0}" -gt 0 ] || continue
    echo "== $(basename "$f" .log): $n lines; first and last log time $(head -1 "$f" | cut -c1-24) .. $(tail -1 "$f" | cut -c1-24)"
    grep -E '"level": "(ERROR|WARNING|CRITICAL)"|"status": (401|50[0-9])|Traceback|Error' "$f" | cut -c1-330 | head -25
  done
  echo; echo "---- first and last line of every pod's log (when each pod ran)"
  for f in "$dir"/logs/*.log; do [ -e "$f" ] && echo "$(basename "$f" .log): $(head -1 "$f" | cut -c1-24) .. $(tail -1 "$f" | cut -c1-24)"; done
}

case "$cmd" in
  start) start ;;
  run) run ;;
  stop) stop ;;
  report) report ;;
  *) echo "usage: $0 start|stop|report <dir>" >&2; exit 2 ;;
esac
