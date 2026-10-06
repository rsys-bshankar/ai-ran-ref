# SmoMtlsCertExpiryImminent

Severity: **critical**. Fires when: a certificate of the service-to-service mutual TLS has less than 3 days left, for 5 minutes. It is the escalation of [SmoMtlsCertExpiring](SmoMtlsCertExpiring.md).
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md).

## Symptom

`smo_mtls_cert_not_after_timestamp_seconds` for `file="cert"` or `file="ca"` is under three days. The 14-day warning was not acted on, or the renewal did not reach this module.

## Impact

In under three days the module is refused by every service that requires a client certificate, and cannot verify the others: gateway calls fail, `/ready` goes red, notifications between modules stop. If it is the CA, all modules at once.

## Diagnosis

As in [SmoMtlsCertExpiring](SmoMtlsCertExpiring.md). Check first whether the files on disk are already new (the metric would then be healthy) and the pod only needs a restart.

```bash
python3 scripts/mtls_certs.py status
docker compose exec <module> python -m smo_shared.mtls probe /ready
kubectl get certificate -n <namespace>
```

## Mitigation

Renew now, restart the modules one at a time, and confirm the metric: the procedure is the Mitigation of [SmoMtlsCertExpiring](SmoMtlsCertExpiring.md). Do not postpone a CA rotation to the last day: it takes three rolling restarts.

## Escalation

Page the platform on-call and the owner of the CA now; agree a maintenance window if the CA has to be rotated.
