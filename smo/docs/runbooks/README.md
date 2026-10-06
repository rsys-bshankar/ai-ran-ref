# Runbooks

One page per alert in `deploy/helm/smo/files/smo-alerts.rules.yaml`; each alert's `runbook_url` annotation points at its page, and `tests_integration/test_alert_rules.py` fails when a page is missing, misnamed, or lacks a section. The targets behind the SLO alerts are in [../SLOS.md](../SLOS.md).

## Template

Every page has the same five sections, in this order:

1. **Symptom**: what the operator sees (the alert, the metric, what callers see).
2. **Impact**: who or what is affected, and how badly.
3. **Diagnosis**: commands that tell the likely causes apart (compose lab; the same with `kubectl` in the chart).
4. **Mitigation**: what to do for each cause, safest first.
5. **Escalation**: who to call and with what.

A new alert starts with a page named exactly like the alert (`<AlertName>.md`).

## Index

| Alert | Severity | Page |
|---|---|---|
| `SmoAuditWriteFailed` | critical | [SmoAuditWriteFailed.md](SmoAuditWriteFailed.md) |
| `SmoGatewayAvailabilityBurnFast` | critical | [SmoGatewayAvailabilityBurnFast.md](SmoGatewayAvailabilityBurnFast.md) |
| `SmoGatewayLatencyBurnFast` | critical | [SmoGatewayLatencyBurnFast.md](SmoGatewayLatencyBurnFast.md) |
| `SmoModuleDown` | critical | [SmoModuleDown.md](SmoModuleDown.md) |
| `SmoMtlsCertExpiryImminent` | critical | [SmoMtlsCertExpiryImminent.md](SmoMtlsCertExpiryImminent.md) |
| `SmoOutboxDeliveryLagBurnFast` | critical | [SmoOutboxDeliveryLagBurnFast.md](SmoOutboxDeliveryLagBurnFast.md) |
| `SmoAuthRefusalsHigh` | warning | [SmoAuthRefusalsHigh.md](SmoAuthRefusalsHigh.md) |
| `SmoDbPoolNearlyExhausted` | warning | [SmoDbPoolNearlyExhausted.md](SmoDbPoolNearlyExhausted.md) |
| `SmoGatewayAvailabilityBurnSlow` | warning | [SmoGatewayAvailabilityBurnSlow.md](SmoGatewayAvailabilityBurnSlow.md) |
| `SmoGatewayLatencyBurnSlow` | warning | [SmoGatewayLatencyBurnSlow.md](SmoGatewayLatencyBurnSlow.md) |
| `SmoModuleHighErrorRate` | warning | [SmoModuleHighErrorRate.md](SmoModuleHighErrorRate.md) |
| `SmoModuleLatencyHigh` | warning | [SmoModuleLatencyHigh.md](SmoModuleLatencyHigh.md) |
| `SmoMtlsCertExpiring` | warning | [SmoMtlsCertExpiring.md](SmoMtlsCertExpiring.md) |
| `SmoOutboundCallsFailing` | warning | [SmoOutboundCallsFailing.md](SmoOutboundCallsFailing.md) |
| `SmoOutboxBacklog` | warning | [SmoOutboxBacklog.md](SmoOutboxBacklog.md) |
| `SmoOutboxDeadRows` | warning | [SmoOutboxDeadRows.md](SmoOutboxDeadRows.md) |
| `SmoOutboxDeliveryLagBurnSlow` | warning | [SmoOutboxDeliveryLagBurnSlow.md](SmoOutboxDeliveryLagBurnSlow.md) |
| `SmoRAppInstancesFaulted` | warning | [SmoRAppInstancesFaulted.md](SmoRAppInstancesFaulted.md) |
| `SmoWorkerTaskFailing` | warning | [SmoWorkerTaskFailing.md](SmoWorkerTaskFailing.md) |
| `SmoGatewayRateLimiting` | info | [SmoGatewayRateLimiting.md](SmoGatewayRateLimiting.md) |

## Not covered by an alert yet

Postgres down (it shows as `SmoModuleDown` for every module and as pool and 5xx alerts; the database itself is not scraped by this chart), the expiry of the edge TLS certificate (`PR-SEC-1`: the deployment's own; the service-to-service certificates have `SmoMtlsCertExpiring`), and backup and restore (`scripts/db_backup.sh`, `scripts/db_restore.sh`, `docs/SECRETS.md`; disaster recovery, `PR-HA-6`). They get a page when the alert or the procedure exists.
