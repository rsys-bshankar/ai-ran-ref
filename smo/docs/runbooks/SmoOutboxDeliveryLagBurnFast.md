# SmoOutboxDeliveryLagBurnFast

Severity: **critical**. Fires when: A module's oldest pending notification has been over 15 minutes old for more than 7.2% of the last hour and of the last 5 minutes: the delivery-lag budget (99.5% of the time under 15 minutes, proposed, `docs/SLOS.md`) burns 14 times too fast.
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md).

## Symptom

`smo_outbox_oldest_pending_age_seconds` is above 900 for a module and stays there. The retry backoff is 755 s in total, so a PENDING row that old is stuck, not retrying.

## Impact

Nothing is sending that module's notifications; every subscriber is behind. Rows are safe, but the events are late by more than the design allows.

## Diagnosis

Commands are for the compose lab; in the chart use `kubectl -n <namespace> logs deploy/<module>` and `kubectl exec` the same way. `<module>` is the `job` label of the alert.

```bash
docker compose exec postgres psql -U smo smo -c "SELECT id, destination, attempts, next_attempt_at, left(last_error, 80), created_at FROM notification_outbox WHERE status = 'PENDING' ORDER BY created_at LIMIT 10"
docker compose ps | grep worker
docker compose logs --since 30m <module>-worker | tail -50
```
`next_attempt_at` far in the future with `attempts` 0 is a lease held by a sender that died (it becomes due again after 60 s); no worker alive is the usual cause.

## Mitigation

- Start or restart the worker: `docker compose up -d <module>-worker` (`kubectl -n smo rollout restart deploy/<module>-worker`); the sweep resumes at once.
- If the worker runs and rows stay PENDING, check the database lock (`pg_stat_activity`) and the worker's log for a failing `outbox-sweep`.
- A single bad destination does not cause this (rows go DEAD after 5 attempts); see `SmoOutboxDeadRows.md`.

## Escalation

Page the platform on-call: the whole delivery path of the module is stopped.
