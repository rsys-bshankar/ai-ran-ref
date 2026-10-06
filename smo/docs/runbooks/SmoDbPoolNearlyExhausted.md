# SmoDbPoolNearlyExhausted

Severity: **warning**. Fires when: More than 90% of a module's database connection pool is in use for 5 minutes.
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md).

## Symptom

`smo_db_pool_connections{state="in_use"}` is near `smo_db_pool_capacity`. Requests queue for a connection and slow down; at the pool timeout they fail with 500 and `QueuePool limit ... reached` in the log.

## Impact

The module's latency and error rate climb (and the gateway's if it is `r1-termination`).

## Diagnosis

Commands are for the compose lab; in the chart use `kubectl -n <namespace> logs deploy/<module>` and `kubectl exec` the same way. `<module>` is the `job` label of the alert.

```bash
docker compose exec postgres psql -U smo smo -c "SELECT usename, state, count(*) FROM pg_stat_activity GROUP BY 1, 2 ORDER BY 3 DESC"
docker compose exec postgres psql -U smo smo -c "SELECT pid, now() - query_start AS age, state, left(query, 80) FROM pg_stat_activity WHERE state <> 'idle' ORDER BY age DESC LIMIT 10"
docker compose exec postgres psql -U smo smo -c "SHOW max_connections"
docker compose logs --since 15m <module> | grep -i 'QueuePool' | tail
```
Many `idle in transaction` connections is a leak; long-running statements are a slow query or a lock; many busy short ones are plain load.

## Mitigation

- Slow statement or lock: cancel it (`SELECT pg_cancel_backend(<pid>)`), then fix the cause (index, the transaction that holds the lock).
- Plain load: raise the pool (`SMO_DB_POOL_SIZE`, `SMO_DB_MAX_OVERFLOW`, see `docs/ARCHITECTURE.md`) within `max_connections` (replicas x workers x pool), or put a pooler in front (`SMO_DB_POOLER`, pgbouncer in the compose lab).
- Restart the module only to clear a leak, and file the leak.

## Escalation

DBA for locks or `max_connections`; the module's team for a leak. Page on-call if the module's error rate also fires.
