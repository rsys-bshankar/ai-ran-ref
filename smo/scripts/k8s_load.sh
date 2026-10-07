#!/usr/bin/env bash
# A load through the gateway, from a pod of its own, for the Kubernetes lanes (PR-V-10, PR-V-12).
#
#   scripts/k8s_load.sh start IMAGE [NODE] [RATE] [CONCURRENCY]   # IMAGE: local/smo-r1-termination:<tag> (has httpx); NODE: where the pod must stay ("" for anywhere)
#   scripts/k8s_load.sh stop OUTDIR                              # ends the load, copies load-results.json and .md to OUTDIR, prints the table
#
# The pod holds the enrollment secret like an SMO module (the chart's Secret), copies scripts/load_run.py in and runs it in the background; `stop` creates its stop file and waits for
# the result. RATE is calls a second in all (0: as fast as the answers come back). A pod without a controller cannot be drained, so pass the node a drain will not touch.
set -euo pipefail
ns=smo
case "${1:-}" in
  start)
    image=${2:?image} node=${3:-} rate=${4:-20} concurrency=${5:-4}
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    { cat <<EOF
apiVersion: v1
kind: Pod
metadata: { name: loadgen, labels: { app.kubernetes.io/name: loadgen } }
spec:
  restartPolicy: Never
EOF
      [ -z "$node" ] || echo "  nodeName: $node"
      cat <<EOF
  containers:
    - name: load
      image: $image
      imagePullPolicy: Never
      command: ["sleep", "10800"]
      volumeMounts: [{ name: secrets, mountPath: /run/secrets, readOnly: true }]
  volumes:
    - name: secrets
      secret: { secretName: smo-secrets, items: [{ key: enrollment-secret, path: enrollment_secret }] }
EOF
    } | kubectl -n "$ns" apply -f -
    kubectl -n "$ns" wait --for=condition=ready pod/loadgen --timeout=120s
    kubectl -n "$ns" exec -i loadgen -- sh -c 'cat > /tmp/load_run.py' < "$here/load_run.py"
    kubectl -n "$ns" exec loadgen -- sh -c "cd /tmp && nohup python load_run.py --concurrency $concurrency --rate $rate --duration 10000 --warmup 5 --stop-file /tmp/stop --out /tmp/out --max-error-rate 1 > /tmp/load.log 2>&1 &"
    sleep 20
    kubectl -n "$ns" exec loadgen -- pgrep -f load_run.py > /dev/null || { kubectl -n "$ns" exec loadgen -- cat /tmp/load.log; echo "::error::the load did not start"; exit 1; }
    ;;
  stop)
    out=${2:?output directory}
    kubectl -n "$ns" exec loadgen -- touch /tmp/stop
    for _ in $(seq 1 60); do kubectl -n "$ns" exec loadgen -- test -f /tmp/out/load-results.json 2> /dev/null && break; sleep 2; done
    mkdir -p "$out"
    kubectl -n "$ns" exec loadgen -- cat /tmp/out/load-results.json > "$out/load-results.json"
    kubectl -n "$ns" exec loadgen -- cat /tmp/out/load-results.md > "$out/load-results.md"
    cat "$out/load-results.md"
    ;;
  *) sed -n 2,9p "$0"; exit 2 ;;
esac
