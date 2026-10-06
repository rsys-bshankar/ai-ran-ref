# SmoGatewayLatencyBurnFast

Severity: **critical**. Fires when: More than 14.4% of gateway requests take longer than 1 s over 1 hour and over 5 minutes: the latency budget (99% under 1 s, proposed, `docs/SLOS.md`) is spent 14 times too fast.
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md).

## Symptom

Calls through R1 are slow rather than failing: callers see timeouts, the GUI is sluggish, p99 of the gateway is above a second.

## Impact

Timeouts turn into retries and then into errors; rApps with tight loops miss their cycles. The budget is gone in about two days at this rate.

## Diagnosis

Commands are for the compose lab; in the chart use `kubectl -n <namespace> logs deploy/<module>` and `kubectl exec` the same way. `<module>` is the `job` label of the alert.

```bash
# slow share and p99 by route (Prometheus)
#   histogram_quantile(0.99, sum by (route, le) (rate(smo_http_request_duration_seconds_bucket{job="r1-termination"}[5m])))
# is the time spent behind the gateway? compare outbound latency by target
#   histogram_quantile(0.99, sum by (target, le) (rate(smo_outbound_call_duration_seconds_bucket{client="r1"}[5m])))
docker compose exec r1-termination python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/metrics').read().decode())" | grep -E 'smo_db_pool'
docker compose logs postgres | grep duration | tail -20          # statements slower than POSTGRES_SLOW_QUERY_MS
docker stats --no-stream
```
If gateway latency is close to one target's latency, the cause is that module (and its database); if the gateway is slow with fast targets, look at CPU limits and the token check (SME).

## Mitigation

- Slow database: kill or fix the slow statement from the Postgres log; add the missing index; check for a lock (`SELECT * FROM pg_stat_activity WHERE state <> 'idle'`).
- CPU-bound module: raise its limit or add replicas (`modules.<module>.replicas`).
- A slow subscriber callback never blocks a request (the outbox sends after commit); do not look there first.
- Shed load: a looping caller shows as one invoker in the gateway log; the rate limit (`R1_RATE_PER_SECOND`) is the control.

## Escalation

Page the platform on-call. Bring in the DBA when slow statements dominate.
