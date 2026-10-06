# SmoModuleHighErrorRate

Severity: **warning**. Fires when: More than 5% of a module's requests are 5xx for 10 minutes, at more than one request every 10 seconds.
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md).

## Symptom

One module (`job`) answers many requests with `500`/`502`/`503`/`504`; the gateway passes them on.

## Impact

Features of that module fail for a share of callers. If the module is the gateway, the SLO burn alerts fire too.

## Diagnosis

Commands are for the compose lab; in the chart use `kubectl -n <namespace> logs deploy/<module>` and `kubectl exec` the same way. `<module>` is the `job` label of the alert.

```bash
docker compose logs --since 15m <module> | grep -E '"level": ?"(ERROR|CRITICAL)"' | tail -50
docker compose exec <module> python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/metrics').read().decode())" | grep -E 'smo_http_requests_total.*status="5|smo_db_pool|smo_outbound_calls_total'
```
Prometheus: `sum by (route, status) (rate(smo_http_requests_total{job="<module>",status=~"5.."}[10m]))`. Typical causes: the database (pool full, slow: `SmoDbPoolNearlyExhausted`), a module it calls (`smo_outbound_calls_total` by `target`), a bug in one route (one route dominates), or a southbound element that does not answer (`ENDPOINT_UNREACHABLE` is a 503 by design).

## Mitigation

- Follow the cause: database (`SmoDbPoolNearlyExhausted.md`), a called module (`SmoModuleDown.md`, `SmoOutboundCallsFailing.md`).
- One route failing after a release: roll back, or ship the fix.
- A 503 `ENDPOINT_UNREACHABLE` from RAN NF OAM is a managed element or the adaptor not answering, not a bug: check the adaptor (`mock-o1-adaptor` in the lab) and the element's endpoint.

## Escalation

Ticket to the owning team; page on-call if it persists for 30 minutes or the module is on the gateway's critical path (`sme`, `onboarding`, `rapp-mgmt`).
