#!/usr/bin/env bash
# Samples `docker stats` every SAMPLE_INTERVAL seconds (default 2) into $1 (JSON lines) until the file $1.stop exists (PR-OPS-9.2). Run in the background around a load run;
# scripts/sizing_report.py reads the result.
set -u
out="${1:?usage: sample_stats.sh <out.jsonl>}"
interval="${SAMPLE_INTERVAL:-2}"
: > "$out"
while [ ! -e "$out.stop" ]; do
  docker stats --no-stream --format '{{json .}}' >> "$out" 2>/dev/null || true
  sleep "$interval"
done
