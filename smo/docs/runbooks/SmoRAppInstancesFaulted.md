# SmoRAppInstancesFaulted

Severity: **warning**. Fires when: `smo_rapp_instances{state="FAULTED"} > 0` for 15 minutes: an rApp instance crashed or failed to bootstrap and was not recovered.
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md).

## Symptom

`rapp-mgmt` reports one or more instances in `FAULTED`. The rApp does not do its work (no KPIs read, no actions taken).

## Impact

The function the rApp provides (energy saving, mobility optimisation, ...) is off for its scope. Other rApps are unaffected.

## Diagnosis

Commands are for the compose lab; in the chart use `kubectl -n <namespace> logs deploy/<module>` and `kubectl exec` the same way. `<module>` is the `job` label of the alert.

```bash
docker compose exec rapp-mgmt python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/metrics').read().decode())" | grep smo_rapp_instances
docker compose exec postgres psql -U smo smo -c "SELECT instance_id, package_id, state, workload_ref, created_at FROM rapp_instance WHERE state = 'FAULTED'"
docker compose logs --since 1h rapp-mgmt | grep -E 'FAULTED|CRASH|BOOTSTRAP_FAILED' | tail -30
kubectl -n smo get pods | grep <rapp>        # in the chart, an rApp is a module
```
Also read the instance's fault reports through the gateway (`GET /rapp-mgmt/instances/{id}/faults`).

## Mitigation

- Transient (the rApp's dependency was down): recover it, `POST /rapp-mgmt/instances/{id}/recover` (the `RECOVER` event), and watch it return to `RUNNING`.
- Bad package or configuration: terminate it (`POST /rapp-mgmt/instances/{id}/terminate`, which revokes its credential) and deploy a corrected one.
- A crash loop of the workload: fix the rApp; do not recover repeatedly.

## Escalation

The rApp's owner (vendor or team). Platform on-call only if several rApps fault together (then look at SME, NFO and the workload platform).
