# SmoGatewayAvailabilityBurnFast

Severity: **critical**. Fires when: More than 1.44% of gateway requests are 5xx over 1 hour and over 5 minutes: the 30-day availability budget (99.9%, proposed, `docs/SLOS.md`) is being spent 14 times too fast.
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md).

## Symptom

Callers (rApps, the GUI backend, operators) get `5xx` from R1 Termination: `502`/`503`/`504` or `500`. The error ratio panel of the gateway is above 1.44%.

## Impact

At this rate the month's error budget is gone in about two days. Every northbound call through R1 is affected in proportion; rApps that retry make it worse.

## Diagnosis

Commands are for the compose lab; in the chart use `kubectl -n <namespace> logs deploy/<module>` and `kubectl exec` the same way. `<module>` is the `job` label of the alert.

```bash
# which routes and statuses (from the gateway's own metrics)
docker compose exec r1-termination python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/metrics').read().decode())" | grep 'smo_http_requests_total.*status="5'
# which backend is the gateway failing to reach (outbound calls from the gateway, by module)
docker compose exec r1-termination python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/metrics').read().decode())" | grep smo_outbound_calls_total | grep -v 'outcome="2xx"'
docker compose logs --since 15m r1-termination | grep -E '"status": ?5' | tail -50
```
Prometheus: `sum by (route, status) (rate(smo_http_requests_total{job="r1-termination",status=~"5.."}[5m]))`. A `503 AUTH_SERVICE_UNAVAILABLE` means SME or its database is down (`SmoModuleDown`); a `502` or `504` on one `/<module>/` prefix means that module is.

## Mitigation

- Find the failing backend from the routes above and follow its runbook (`SmoModuleDown.md`, `SmoDbPoolNearlyExhausted.md`, `SmoOutboundCallsFailing.md`).
- If one replica of the gateway is bad, restart or remove it: `kubectl -n smo delete pod <pod>`.
- A recent release: roll back (chart README, "Rolling back").
- Do not raise the rate limit to "fix" 5xx; 429 is not counted in this SLI.

## Escalation

Page the platform on-call immediately (this is the page-worthy burn). If the cause is the database, also the DBA. Open an incident; record start time and the error ratio, for the SLO report.
