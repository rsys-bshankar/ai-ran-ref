# SmoWalArchiveLate

Severity: **critical**. Fires when: the newest archived WAL segment of the CloudNativePG cluster is over 900 seconds old (the recovery point objective), for 10 minutes.
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md). The targets behind it: `docs/DISASTER_RECOVERY.md` (RPO 15 minutes, RTO 1 hour).

## Symptom

`cnpg_pg_stat_archiver_seconds_since_last_archival` is over 900 on the primary. With `archive_timeout` set, even an idle database ships a segment every few minutes, so a value this high means archiving has stopped, not that nothing happened.

## Impact

A loss of the whole database now loses more than the 15 minutes the recovery point objective promises: everything since the last archived segment. Segments that cannot be shipped stay on the instance's disk, which fills.

## Diagnosis

On the Kubernetes chart with CloudNativePG; the metrics are the operator's, read from each instance's port 9187.

```bash
kubectl -n <namespace> get cluster <name> -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.message}{"\n"}{end}'     # ContinuousArchiving should be True
kubectl -n <namespace> exec <primary> -c postgres -- psql -U postgres -c "SELECT archived_count, failed_count, last_archived_wal, last_archived_time, last_failed_wal, last_failed_time FROM pg_stat_archiver"
kubectl -n <namespace> logs <primary> -c postgres --tail=100 | grep -i -E "archiv|barman"
kubectl -n <namespace> exec <primary> -c postgres -- df -h /var/lib/postgresql/data      # the disk the unshipped segments fill
```


## Mitigation

- The object store is unreachable or refuses (credentials rotated, bucket gone, quota, a changed endpoint): fix that; Postgres retries the same segment by itself and the alert clears when it ships.
- `ContinuousArchiving` is False because the Cluster has no `spec.backup.barmanObjectStore` or a wrong Secret name: correct the Cluster; the operator reconfigures the instances.
- The disk is nearly full of unshipped WAL: extend the volume first (the cluster's `spec.storage.size`), then fix the archive; never delete `pg_wal` files by hand.

## Escalation

Platform on-call at once (this is the recovery point); the owner of the object store.
