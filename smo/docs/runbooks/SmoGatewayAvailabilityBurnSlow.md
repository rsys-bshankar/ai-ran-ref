# SmoGatewayAvailabilityBurnSlow

Severity: **warning**. Fires when: More than 0.6% of gateway requests are 5xx over 6 hours and over 30 minutes: the availability budget is being spent 6 times too fast (gone in 5 days).
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md).

## Symptom

A steady, lower rate of `5xx` through the gateway: a flaky backend, one route that always fails, or a dependency that times out now and then. Nobody is necessarily paged by callers yet.

## Impact

Not an outage, but the month's budget will run out in days if it continues; the slow burn is what usually precedes a fast one.

## Diagnosis

Commands are for the compose lab; in the chart use `kubectl -n <namespace> logs deploy/<module>` and `kubectl exec` the same way. `<module>` is the `job` label of the alert.

```bash
# the 5xx by route over the last hour (Prometheus)
#   topk(10, sum by (route, status) (increase(smo_http_requests_total{job="r1-termination",status=~"5.."}[1h])))
docker compose logs --since 1h r1-termination | grep -E '"status": ?5' | tail -100
# which outbound target fails
docker compose exec r1-termination python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/metrics').read().decode())" | grep smo_outbound_calls_total | grep -E 'outcome="(5xx|timeout|error)"'
```
Look for one route or one module dominating; compare with the time of the last deploy.

## Mitigation

- Fix or roll back whatever the dominant route points at (a release, a module, a subscriber that times out).
- If the errors are a client's bad requests answered `500` (a bug), file it against the module and treat it as the fix; the budget only recovers when they stop.
- If nothing can be done now, record why in the ticket and watch that the fast-burn alert does not follow.

## Escalation

Ticket for the owning module's team during working hours; escalate to on-call if `SmoGatewayAvailabilityBurnFast` fires or the 5-day budget forecast shortens.
