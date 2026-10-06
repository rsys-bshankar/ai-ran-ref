# SmoModuleLatencyHigh

Severity: **warning**. Fires when: A module's p99 request duration is above 5 s for 15 minutes (at more than one request every 10 seconds).
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md).

## Symptom

One module is slow; its callers' timeouts (R1Client's, or an rApp's) start to fire.

## Impact

Slow features in that module, and a latency burn at the gateway if the module is called often.

## Diagnosis

Commands are for the compose lab; in the chart use `kubectl -n <namespace> logs deploy/<module>` and `kubectl exec` the same way. `<module>` is the `job` label of the alert.

```bash
docker compose exec <module> python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/metrics').read().decode())" | grep -E 'smo_db_pool|smo_outbound_call_duration_seconds'
docker compose logs postgres | grep duration | tail -30
docker stats --no-stream <module>
```
Prometheus: `histogram_quantile(0.99, sum by (route, le) (rate(smo_http_request_duration_seconds_bucket{job="<module>"}[10m])))` names the route. RAN NF OAM southbound writes retry inside the request (a bounded time budget, module README), so a slow or dead managed element shows here.

## Mitigation

- A southbound element that is slow or unreachable: check it, or lower the retry budget.
- A slow statement: index or rewrite it.
- CPU or memory limits too tight: raise them, or add replicas.

## Escalation

Ticket to the owning team; on-call if it spreads to the gateway (`SmoGatewayLatencyBurnFast`).
