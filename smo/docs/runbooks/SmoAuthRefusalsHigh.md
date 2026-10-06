# SmoAuthRefusalsHigh

Severity: **warning**. Fires when: A module refuses more than 5 requests per second as `unauthorized` or `forbidden` for 10 minutes.
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md).

## Symptom

`smo_refusals_total{reason=~"unauthorized|forbidden"}` is high for a module (usually `r1-termination`): `401` (bad, expired or revoked token) or `403` (role or scope refused).

## Impact

The refused callers cannot work. It may also be probing: a credential scan looks like this.

## Diagnosis

Commands are for the compose lab; in the chart use `kubectl -n <namespace> logs deploy/<module>` and `kubectl exec` the same way. `<module>` is the `job` label of the alert.

```bash
docker compose logs --since 15m r1-termination | grep -E '"status": ?(401|403)' | tail -30
docker compose exec r1-termination python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/metrics').read().decode())" | grep -E 'smo_refusals_total|smo_role_refusals_total'
```
One invoker repeating: a misconfigured or revoked rApp (a terminated instance whose process still runs). Many sources and many paths: probing. A burst of `401` right after a restart of SME: token cache or key mismatch (`503 AUTH_SERVICE_UNAVAILABLE` is separate and is not a refusal).

## Mitigation

- Misconfigured rApp: fix its credential or scope (`docs/RAPP_PACKAGING.md`); stop the process of a terminated instance.
- Probing: block the source at the edge (ingress / firewall); the gateway already refuses.
- After SME or key rotation: restart the callers so they fetch new tokens.

## Escalation

Security contact if sources are unknown or the volume is not explained within 30 minutes.
