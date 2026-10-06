# SmoOutboundCallsFailing

Severity: **warning**. Fires when: More than 20% of outbound calls to one target fail (5xx, timeout, connection error) for 10 minutes.
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md).

## Symptom

`smo_outbound_calls_total{client,target,outcome}` shows `5xx`, `timeout` or `error` for one target: either a module reached through R1 (`client="r1"`, `target="sme"`, `"nfo"`, ...) or caller-registered callbacks (`client="webhook"`, `target="callback"`).

## Impact

`client="r1"`: the calling module's features that need that target fail (for example `onboarding` cannot reach `nfo`; `ran-nf-oam` cannot reach its adaptor). `client="webhook"`: subscribers miss events, and the rows retry through the outbox (`SmoOutboxBacklog`, `SmoOutboxDeadRows`).

## Diagnosis

Commands are for the compose lab; in the chart use `kubectl -n <namespace> logs deploy/<module>` and `kubectl exec` the same way. `<module>` is the `job` label of the alert.

```bash
# Prometheus: who calls whom and how it ends
#   sum by (job, client, target, outcome) (rate(smo_outbound_calls_total[10m]))
docker compose ps <target>
docker compose logs --since 15m <target> | tail -50
docker compose logs --since 15m r1-termination | grep -E '"target": ?"<target>"' | tail -20
```
Metrics are per caller process: add `job` to see which module is calling. A target that is up and healthy but slow shows as `timeout`; `blocked` (not in this alert) is the SSRF guard.

## Mitigation

- `client=r1`: restore the target module (`SmoModuleDown.md`); check R1's routing table has it (`/<target>` is not `404 NO_ROUTE`).
- `client=webhook`: the subscribers' endpoints are failing; there is nothing to fix here beyond telling their owners. Deliveries are retried by the outbox.
- A target that is slow because of its own database: `SmoDbPoolNearlyExhausted.md`.

## Escalation

The target module's team (for `r1`) or the subscriber's owner (for `webhook`); on-call when the target is on the gateway's critical path.
