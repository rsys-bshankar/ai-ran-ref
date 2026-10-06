# SmoMtlsCertExpiring

Severity: **warning**. Fires when: a certificate of the service-to-service mutual TLS (PR-SEC-2) has less than 14 days left, for an hour.
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md). Procedure behind it: `docs/ARCHITECTURE.md`, "Mutual TLS between services".

## Symptom

`smo_mtls_cert_not_after_timestamp_seconds{file="cert"}` (the module's own certificate) or `{file="ca"}` (the earliest CA certificate it trusts) minus `time()` is under 14 days. The metric is read from the module's files when Prometheus scrapes, so it follows the file on disk, not the certificate the running server loaded at start.

## Impact

None yet. When a leaf certificate expires, every service that requires a client certificate refuses that module and the module's own verification of the others fails: calls through the gateway fail with 502 or 401 and `/ready` goes red. When the CA expires, every service loses every other.

## Diagnosis

Compose lab; in the chart use `kubectl -n <namespace>` and, with cert-manager, `kubectl get certificate`.

```bash
python3 scripts/mtls_certs.py status                                   # days left on the CA and on every certificate (exit 1 under 30)
docker compose exec sme python -m smo_shared.mtls probe /ready          # does this module still talk to itself over mTLS
kubectl get certificate -n <namespace>                                  # cert-manager: READY, and "Renewal Time" in `kubectl describe`
```
A file already renewed on disk but a pod not restarted shows here as a healthy metric and an old certificate in the running server: restart the pod (servers load their files at start; clients reread them when they change).

## Mitigation

- Compose: `python3 scripts/mtls_certs.py renew`, then `docker compose -f docker-compose.yml -f docker-compose.mtls.yml up -d --force-recreate` (one service at a time keeps the stack answering).
- Chart with cert-manager: it renews on its own at two thirds of the lifetime; if it did not, read `kubectl describe certificate` (issuer not ready, quota), fix that, then `kubectl rollout restart deploy/<module>`.
- Chart with Secrets you made: issue new certificates from your CA, update each module's Secret, `kubectl rollout restart` the modules one at a time.
- A CA about to expire: the three-phase rotation, `scripts/mtls_certs.py rotate-ca trust|issue|retire` with a rolling restart after each phase.

## Escalation

Platform on-call; the owner of the CA (security) if the CA itself is the one expiring.
