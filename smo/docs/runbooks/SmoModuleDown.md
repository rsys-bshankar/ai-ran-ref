# SmoModuleDown

Severity: **critical**. Fires when: `up{job=...} == 0` for 2 minutes: Prometheus cannot scrape a module's `/metrics`.
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md).

## Symptom

`SmoModuleDown` fires for one `job` (a module) and one `instance`. Calls that need that module fail or time out, and the gateway answers `502`/`503` for its routes (`/<module>/...`).

## Impact

Everything the module does is unavailable. By module: **r1-termination** is the whole northbound R1 interface (see also the gateway burn-rate alerts); **sme** stops token checks, so the gateway answers `503 AUTH_SERVICE_UNAVAILABLE` to every caller; **onboarding**, **rapp-mgmt**, **nfo** and **focom** stop package and rApp lifecycle changes; modules that use the database cannot serve if Postgres is down (then many modules fire at once: start with the database).

## Diagnosis

Commands are for the compose lab; in the chart use `kubectl -n <namespace> logs deploy/<module>` and `kubectl exec` the same way. `<module>` is the `job` label of the alert.

```bash
# which modules are not up, and why (compose)
docker compose ps
docker compose logs --since 15m <module> | tail -100
# Kubernetes
kubectl -n smo get pods -l app.kubernetes.io/name=<module>
kubectl -n smo describe pod <pod> | tail -30        # OOMKilled, CrashLoopBackOff, failed probe, image pull
kubectl -n smo logs <pod> --previous | tail -100
# is it the module or the scrape? ask the module directly
docker compose exec <module> python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/ready').read())"
# is the database reachable (many modules down at once)?
docker compose exec postgres pg_isready -U smo
```
A module that never becomes ready is waiting for the schema (`migrate` did not finish) or for the database: `docker compose logs migrate`.

## Mitigation

- Restart the module: `docker compose restart <module>` or `kubectl -n smo rollout restart deploy/<module>`.
- `OOMKilled`: raise the memory limit (`modules.<module>.resources.limits.memory` in the chart) and look for the request that grew it.
- Several modules down together: fix Postgres first (disk full, `max_connections`, failover; `docs/runbooks/SmoDbPoolNearlyExhausted.md`), then they recover on their own.
- A bad release: `helm rollback` to a revision made by `helm upgrade` (chart README, "Rolling back").
- If only a scrape is broken (the module answers `/ready`), fix the scrape target or network policy, not the module.

## Escalation

Page the platform on-call when the gateway (`r1-termination`), `sme` or Postgres is down, or when a restart does not bring the module back within 10 minutes. Attach the last 100 log lines and the `describe pod` output.
