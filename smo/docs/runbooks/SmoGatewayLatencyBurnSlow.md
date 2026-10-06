# SmoGatewayLatencyBurnSlow

Severity: **warning**. Fires when: More than 6% of gateway requests take longer than 1 s over 6 hours and over 30 minutes: latency budget burning 6 times too fast.
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md).

## Symptom

A persistent share of slow requests through the gateway; the p95 has crept up. No outage.

## Impact

The budget is spent in 5 days if it continues; callers with short timeouts see occasional failures.

## Diagnosis

Commands are for the compose lab; in the chart use `kubectl -n <namespace> logs deploy/<module>` and `kubectl exec` the same way. `<module>` is the `job` label of the alert.

```bash
# the routes that are slow (Prometheus)
#   topk(10, histogram_quantile(0.95, sum by (route, le) (rate(smo_http_request_duration_seconds_bucket{job="r1-termination"}[30m]))))
docker compose logs postgres | grep duration | tail -50
docker compose exec r1-termination python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/metrics').read().decode())" | grep -E 'smo_db_pool|smo_outbound_call_duration_seconds_sum'
```
Look at table growth (alarms, performance files: `docs/PERFORMANCE.md`) and at list routes with `?total=true` on a large table.

## Mitigation

- Add the missing index or trim the data (retention) that makes the list route slow.
- Paginate callers that read whole lists.
- Re-measure with the load harness (`scripts/load_run.py`, `docs/PERFORMANCE.md`) after the change.

## Escalation

Ticket to the owning module's team; page only if `SmoGatewayLatencyBurnFast` fires.
