# SmoBackupLate

Severity: **warning**. Fires when: the newest base backup of the CloudNativePG cluster is older than 26 hours (the daily schedule plus two), for 15 minutes.
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md). The targets behind it: `docs/DISASTER_RECOVERY.md` (RPO 15 minutes, RTO 1 hour).

## Symptom

`time() - cnpg_collector_last_available_backup_timestamp` is over 26 hours, or the metric is `0`: no backup was ever completed. The cluster's `status.lastSuccessfulBackup` says the same.

## Impact

None on the running database. A recovery still works as long as the WAL is archived (SmoWalArchiveLate quiet), but it starts from an older base backup and replays more WAL, so the recovery time (RTO 1 hour) grows with every day without a backup.

## Diagnosis

On the Kubernetes chart with CloudNativePG; the metrics are the operator's, read from each instance's port 9187.

```bash
kubectl -n <namespace> get scheduledbackup,backup                                # is there a schedule, and how did the last runs end
kubectl -n <namespace> describe backup <newest>                                  # the error of a failed one: bucket, credentials, endpoint
kubectl -n <namespace> get cluster <name> -o jsonpath='{.status.lastSuccessfulBackup}{"\n"}'
kubectl -n cnpg-system logs deploy/cnpg-controller-manager --tail=100 | grep -i backup
```
The chart renders the schedule only with `postgres.cnpgBackup.enabled=true` and `postgres.cnpgBackup.cluster=<name>`; without it nothing takes a base backup.

## Mitigation

- No `ScheduledBackup`: enable it (`postgres.cnpgBackup.enabled`) and run one now (`kubectl cnpg backup <cluster>` or apply a `Backup` object).
- A failed backup: fix what `describe backup` says (the object store's credentials Secret, the endpoint, a full bucket, a bucket policy) and run one by hand to prove it.
- The schedule exists and ran but the metric is old: the instance's metrics are not scraped (`up` for the pod), or the Cluster has no `spec.backup.barmanObjectStore`.

## Escalation

Platform on-call; the owner of the object store if it refuses writes.
