# SmoAuditWriteFailed

Severity: **critical**. Fires when: The gateway failed to add a row to the audit chain in the last 10 minutes.
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md).

## Symptom

`smo_audit_writes_total{outcome="failed"}` increased. The gateway logs an audit write failure; the request itself was served.

## Impact

The tamper-evident audit record (PR-SEC-11) has a gap: calls happened that the chain does not describe. This is a compliance event, not only an availability one.

## Diagnosis

Commands are for the compose lab; in the chart use `kubectl -n <namespace> logs deploy/<module>` and `kubectl exec` the same way. `<module>` is the `job` label of the alert.

```bash
docker compose logs --since 30m r1-termination | grep -i audit | tail -50
docker compose exec postgres psql -U smo smo -c "SELECT max(occurred_at), max(seq), count(*) FROM audit_log"
docker compose exec postgres psql -U smo smo -c "SELECT now() - pg_postmaster_start_time() AS uptime, pg_is_in_recovery()"
```
A database in recovery (read-only), a full disk, or a revoked grant on the audit table are the usual causes. Once writes work again, check the chain: `docker compose exec r1-termination python -m smo_shared.audit verify` (exit 1 names the first broken link).

## Mitigation

- Restore the database write path (disk, failover, grants) and confirm `smo_audit_writes_total{outcome="ok"}` moves.
- Record the window (first and last failure time, from the logs) in the incident: the gap is part of the record.
- Do not edit the chain by hand.

## Escalation

Page the platform on-call and the security contact at once; the window of missing audit rows must be reported under your audit policy.
