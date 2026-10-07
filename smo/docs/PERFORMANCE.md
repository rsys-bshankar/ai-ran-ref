# Performance

What the stack does under load, measured the same way each release so a regression is a difference between two tables, not an opinion.

## How it is measured (V-8a)

`scripts/load_run.py` runs in a one-shot container on the compose network (`.github/workflows/smo-load.yml`, nightly and on demand; `docker-compose.load.yml` turns the gateway's rate limiter off, `R1_RATE_PER_SECOND=0`, which is on by default). It registers one invoker, takes a token, and `--concurrency` workers each pick a route by weight and call it as soon as the last answer is back (a closed loop: the concurrency is the number of calls in flight). The mix: service discovery, alarm list, KPI definitions, config job list, rApp instances, package list, data types, a config job dry run and the token endpoint. A call fails on a 5xx or any status other than the one the route is expected to give; the run exits 1 above `--max-error-rate` (default 0).

The report has, per route and overall: requests, requests/s, p50 / p95 / p99 / max latency (ms), errors; the job summary adds CPU and memory per container and Postgres connections after the run. The tables are kept as the artifact `load-results` for 30 days.

GitHub's runners vary from night to night: compare a run with the previous one on the same kind of runner, never with an absolute figure.

## Data volume (V-8b)

`scripts/load_seed.py --elements N` loads N managed elements with 10 alarms and 5 performance files each (COPY, as the database owner; `--clean` removes them). The load workflow takes `elements` (1000, 10000 or 100000) when run by hand and seeds before the load, so the alarm and PM routes are measured against a table of that size, not an empty one. The nightly run seeds 1000.

## The page total

Every list route counts the whole result for `total` (`COUNT(*)`, 55 ms for a million alarms, linear: `scripts/db_volume_check.py` times it). A caller that only pages forward adds `?total=false`: no count query runs, `total` is left out of the response and `hasMore` says whether another page follows. The volume lane's count cases measure what a default call pays; the opt-out costs a first page.

## Not yet covered

- Cells, managed objects and KPI results are not seeded yet (V-8b seeds managed elements, alarms and performance files).
- Stress and failure injection beyond the first four scenarios (`scripts/stress_run.py`, `.github/workflows/smo-stress.yml`: limiter burst, oversized body, saturation ramp, Postgres down and back): a slow or dead webhook subscriber, SME down, a full package volume (V-9), and the soak (V-9b).
- Baseline numbers per release: the first is below (0.5.0). Per-container CPU and memory from the same run, and what the chart's `resources` are set from, are in `docs/SIZING.md`.

## Baselines

| Release | Runner | Concurrency | Requests/s | p95 (ms) | p99 (ms) | Errors |
|---|---|---|---|---|---|---|
| 0.5.0 | GitHub ubuntu-latest, 10 000 elements, 120 s (run 37570638155) | 20 | 38.9 | 682 | 772 | 0 |
