# Performance

What the stack does under load, measured the same way each release so a regression is a difference between two tables, not an opinion.

## How it is measured (V-8a)

`scripts/load_run.py` runs in a one-shot container on the compose network (`.github/workflows/smo-load.yml`, nightly and on demand; `docker-compose.load.yml` turns the gateway's rate limiter off, `R1_RATE_PER_SECOND=0`, which is on by default). It registers one invoker, takes a token, and `--concurrency` workers each pick a route by weight and call it as soon as the last answer is back (a closed loop: the concurrency is the number of calls in flight). The mix: service discovery, alarm list, KPI definitions, config job list, rApp instances, package list, data types, a config job dry run and the token endpoint. A call fails on a 5xx or any status other than the one the route is expected to give; the run exits 1 above `--max-error-rate` (default 0).

The report has, per route and overall: requests, requests/s, p50 / p95 / p99 / max latency (ms), errors; the job summary adds CPU and memory per container and Postgres connections after the run. The tables are kept as the artifact `load-results` for 30 days.

GitHub's runners vary from night to night: compare a run with the previous one on the same kind of runner, never with an absolute figure.

## Not yet covered

- The data volumes: an empty-ish database is measured today. A seed script for 1k / 10k / 100k managed elements, cells, PM records and alarms is next (V-8b).
- Stress, saturation and failure injection (V-9) and the soak (V-9b).
- Baseline numbers per release: recorded here once the seeded run exists.

## Baselines

| Release | Runner | Concurrency | Requests/s | p95 (ms) | p99 (ms) | Errors |
|---|---|---|---|---|---|---|
| 0.5.0 | (first nightly run) | 20 | | | | |
