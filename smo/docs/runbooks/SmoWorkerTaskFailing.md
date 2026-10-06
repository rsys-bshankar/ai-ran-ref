# SmoWorkerTaskFailing

Severity: **warning**. Fires when: A worker's periodic task failed at least 3 times in 15 minutes.
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md).

## Symptom

`smo_worker_task_runs_total{outcome="failed"}` for `module`/`task` increases. The worker logs `worker task failed` with a traceback and retries after `SMO_WORKER_FAILURE_BACKOFF_SECONDS` (30 s). This series exists only when the worker serves metrics (`SMO_WORKER_METRICS_PORT`) and is scraped.

## Impact

The task's periodic work is not being done: for example `advance-waves`, `publish-kpis`, `run-kpi-guards` or the delivery sweep `outbox-sweep` (then `SmoOutboxDeliveryLagBurnFast` follows).

## Diagnosis

Commands are for the compose lab; in the chart use `kubectl -n <namespace> logs deploy/<module>` and `kubectl exec` the same way. `<module>` is the `job` label of the alert.

```bash
docker compose logs --since 30m <module>-worker | grep -B2 -A20 'worker task failed' | tail -60
docker compose exec postgres psql -U smo smo -c "SELECT name, last_run_at FROM periodic_run ORDER BY last_run_at"
```
The traceback names the cause: a database error, an unreachable module, or a bug in the task.

## Mitigation

- Fix the cause named by the traceback (database, the module it calls, bad data it trips on).
- Tasks are idempotent: once fixed it picks up what is due; there is nothing to re-run by hand.
- A bad release: roll back the worker (the same image as the module).

## Escalation

The owning module's team; platform on-call if the failing task is `outbox-sweep`.
