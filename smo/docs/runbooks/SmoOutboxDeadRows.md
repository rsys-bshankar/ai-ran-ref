# SmoOutboxDeadRows

Severity: **warning**. Fires when: `smo_outbox_rows{status="DEAD"} > 0` for 10 minutes: notifications to a subscriber's destination were given up (5 failed attempts, or the SSRF guard refused it at send time).
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md).

## Symptom

A module has rows in `notification_outbox` with `status = 'DEAD'`. Someone who subscribed to events (a callback URL) stopped receiving them, with no error on the request that caused the event.

## Impact

Lost events for that subscriber: a consumer's view of packages, alarms, jobs or reports can be stale until it re-reads. Other subscribers are unaffected.

## Diagnosis

Commands are for the compose lab; in the chart use `kubectl -n <namespace> logs deploy/<module>` and `kubectl exec` the same way. `<module>` is the `job` label of the alert.

```bash
docker compose exec postgres psql -U smo smo -c "SELECT module, destination, method, attempts, left(last_error, 80) AS last_error, created_at FROM notification_outbox WHERE status = 'DEAD' ORDER BY created_at DESC LIMIT 50"
docker compose exec postgres psql -U smo smo -c "SELECT destination, count(*) FROM notification_outbox WHERE status = 'DEAD' GROUP BY destination ORDER BY 2 DESC"
```
`last_error` says why: a timeout or connection refused (the subscriber is down or gone), an HTTP 5xx, or `blocked` (the destination resolves to an address the SSRF guard refuses, e.g. a private range).

## Mitigation

- Subscriber was down and is back: re-queue its rows (at-least-once delivery, so a duplicate is possible and consumers must cope): `UPDATE notification_outbox SET status = 'PENDING', attempts = 0, next_attempt_at = now() WHERE status = 'DEAD' AND destination = '<url>'`.
- Subscriber is gone: delete its rows (`DELETE FROM notification_outbox WHERE status = 'DEAD' AND destination = '<url>'`) and ask the owner to delete the subscription.
- `blocked`: the destination is not allowed from here; the subscriber must register a reachable public address (`docs/NOTIFICATIONS.md`).

## Escalation

Tell the subscription's owner (the rApp or system that registered the destination). Escalate to the module's team if rows go DEAD for destinations that are healthy: that is a sender bug.
