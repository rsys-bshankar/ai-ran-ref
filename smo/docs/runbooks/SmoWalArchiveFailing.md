# SmoWalArchiveFailing

Severity: **warning**. Fires when: archiving a WAL segment failed at least once in the last 15 minutes, for 5 minutes.
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md). The targets behind it: `docs/DISASTER_RECOVERY.md` (RPO 15 minutes, RTO 1 hour).

## Symptom

`increase(cnpg_pg_stat_archiver_failed_count[15m]) > 0`: segments are being refused by the archive command.

## Impact

None yet if the next attempt works. If it keeps failing, SmoWalArchiveLate follows within 15 minutes and the disk of the instance starts to fill with segments that cannot be shipped.

## Diagnosis

On the Kubernetes chart with CloudNativePG; the metrics are the operator's, read from each instance's port 9187.

```bash
kubectl -n <namespace> exec <primary> -c postgres -- psql -U postgres -c "SELECT failed_count, last_failed_wal, last_failed_time FROM pg_stat_archiver"
kubectl -n <namespace> logs <primary> -c postgres --tail=100 | grep -i -E "archiv|barman|error"
kubectl -n <namespace> get secret <backup-credentials> -o jsonpath='{.metadata.name}{"\n"}'      # does the Secret the Cluster names exist
```


## Mitigation

- Credentials or endpoint: correct the Secret or `spec.backup.barmanObjectStore.endpointURL`; the operator rolls the change out, and the failed segment is retried.
- A bucket that is full or has a retention rule that denies writes: free space or fix the policy.
- A transient error that cleared by itself: no action; the alert clears when no failure falls in 15 minutes.

## Escalation

Platform on-call; the owner of the object store if it is the cause.
