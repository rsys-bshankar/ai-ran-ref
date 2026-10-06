# SmoOutboxBacklog

Severity: **warning**. Fires when: More than 500 `PENDING` rows in a module's outbox for 15 minutes.
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md).

## Symptom

`smo_outbox_rows{status="PENDING"}` is high and not draining; notifications arrive late.

## Impact

Subscribers see events late (or not yet). The rows are safe in the database; nothing is lost while they are PENDING.

## Diagnosis

Commands are for the compose lab; in the chart use `kubectl -n <namespace> logs deploy/<module>` and `kubectl exec` the same way. `<module>` is the `job` label of the alert.

```bash
docker compose exec postgres psql -U smo smo -c "SELECT module, count(*), min(created_at) AS oldest, max(attempts) FROM notification_outbox WHERE status = 'PENDING' GROUP BY module"
docker compose exec postgres psql -U smo smo -c "SELECT destination, count(*) FROM notification_outbox WHERE status = 'PENDING' GROUP BY destination ORDER BY 2 DESC LIMIT 10"
docker compose ps | grep worker
docker compose logs --since 15m <module>-worker | tail -50
```
One destination holding most rows: a slow or dead subscriber (its rows retry with a backoff, others are sent concurrently). All destinations: no worker is running the delivery sweep (`SMO_OUTBOX_SWEEP`, `smo_shared.worker`), or a burst of events outran it.

## Mitigation

- No worker running: start it (`docker compose up -d <module>-worker`; in the chart the `*-worker` module). Any module's worker runs the sweep for the whole table.
- Burst: it drains on its own; raise `SMO_OUTBOX_SEND_CONCURRENCY` (default 8) or `SMO_OUTBOX_SWEEP_SECONDS` lower to speed it up.
- One dead destination: see `SmoOutboxDeadRows.md` (its rows will go DEAD after 5 attempts).

## Escalation

Platform on-call if the sweep is running and the backlog still grows for an hour; otherwise the subscriber's owner.
