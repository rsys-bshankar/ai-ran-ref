# SmoOutboxDeliveryLagBurnSlow

Severity: **warning**. Fires when: The oldest pending notification of a module was over 15 minutes old for more than 3% of the last 6 hours and of the last 30 minutes: the delivery-lag budget burns 6 times too fast.
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md).

## Symptom

The delivery sweep is intermittently stopped or very slow: lag spikes above 15 minutes, recovers, spikes again.

## Impact

Subscribers see events late at times. Not an outage, but the monthly budget (0.5% of the time) is spent in about 5 days.

## Diagnosis

Commands are for the compose lab; in the chart use `kubectl -n <namespace> logs deploy/<module>` and `kubectl exec` the same way. `<module>` is the `job` label of the alert.

```bash
docker compose logs --since 6h <module>-worker | grep -E 'outbox-sweep|worker task failed' | tail -50
docker compose exec postgres psql -U smo smo -c "SELECT status, count(*) FROM notification_outbox GROUP BY status"
```
Prometheus: `max by (module) (smo_outbox_oldest_pending_age_seconds)` over 6 h shows when it spiked; match that to worker restarts (`kubectl -n smo get pods | grep worker`) and to deploys.

## Mitigation

- Worker restarts or evictions: give it resources and a PodDisruptionBudget; run two workers (they share work; no leader).
- Sweep failing now and then: fix the cause in its log (database timeouts, lock waits).
- A very slow sweep: raise `SMO_OUTBOX_SEND_CONCURRENCY`.

## Escalation

Ticket to the platform team; page only if `SmoOutboxDeliveryLagBurnFast` fires.
