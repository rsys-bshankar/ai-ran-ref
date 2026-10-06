# Service level objectives

Status: **every target below is proposed** (`PR-OBS-5`). They are starting points chosen from what the code can measure today, not commitments made to anyone; they become real when an operator accepts them for a deployment, after a few weeks of its own traffic (`docs/PERFORMANCE.md` has the lab numbers). The alert rules that implement them are in `deploy/helm/smo/files/smo-alerts.rules.yaml`, each with a page in [runbooks/](runbooks/README.md).

## What is measured, and where

The SLIs use only series the modules already export (`smo_shared/metrics.py`, see "Metrics" in `docs/ARCHITECTURE.md`), scraped per module as `job="<module>"`. The gateway is `r1-termination`: every northbound call goes through it, so its numbers are what a caller experiences.

| SLO | SLI (good / valid events) | Series |
|---|---|---|
| `gateway-availability` | Requests not answered with a 5xx / all requests. A 4xx is the caller's (bad token, refused role, `429` rate limit) and counts as good. | `smo_http_requests_total{job="r1-termination"}` by `status` |
| `gateway-latency` | Requests answered in 1 s or less / all requests. | `smo_http_request_duration_seconds_bucket{le="1.0"}` over `_count` |
| `outbox-delivery-lag` | Time during which a module's oldest pending notification is 15 minutes old or less / all time. Retries back off for 755 s in total, so older means no sender is working. | `smo_outbox_oldest_pending_age_seconds` |
| `audit-completeness` | Audit rows stored / audit rows attempted. | `smo_audit_writes_total{outcome}` |

Probes and `/metrics` are not counted in the HTTP series. Each module's counters are per process, so an SLI summed over replicas (`sum(rate(...))`) is the right aggregate; the outbox gauges are the same on every replica of a module, so they are aggregated with `max`.

## Targets (proposed)

| SLO | Target (proposed) | Window | Error budget | Alerts |
|---|---|---|---|---|
| `gateway-availability` | 99.9% of requests not 5xx | 30 days | 0.1% of requests (about 43 minutes of total outage) | `SmoGatewayAvailabilityBurnFast`, `SmoGatewayAvailabilityBurnSlow` |
| `gateway-latency` | 99% of requests in 1 s or less | 30 days | 1% of requests | `SmoGatewayLatencyBurnFast`, `SmoGatewayLatencyBurnSlow` |
| `outbox-delivery-lag` | Oldest pending notification at most 15 minutes old, 99.5% of the time | 30 days | 0.5% of time (about 3.6 hours) | `SmoOutboxDeliveryLagBurnFast`, `SmoOutboxDeliveryLagBurnSlow` |
| `audit-completeness` | 100% of audit rows stored | any | none: a gap is an incident | `SmoAuditWriteFailed` (no burn rate: one failure is enough) |

Latency percentiles: the 1 s line is the p99 target stated as a ratio (a ratio of slow requests can be summed across replicas and windows; a percentile cannot). For reading, `histogram_quantile(0.95, ...)` and `0.99` of `smo_http_request_duration_seconds_bucket` are on the dashboards; a per-module p99 above 5 s for 15 minutes is its own warning (`SmoModuleLatencyHigh`). Not yet an SLO: per-route targets (a list route over a million rows is slower by design, `docs/PERFORMANCE.md`), and anything for a module other than the gateway.

## Burn-rate alerts

A burn rate is how many times faster than "exactly on target" the budget is being spent: error ratio divided by the budget. Each SLO has the two alerts of the multiwindow, multi-burn-rate method (Google SRE Workbook, "Alerting on SLOs"): both a long and a short window must be over the line, so a spike that has ended does not page and a slow leak does not go unseen.

| Alert | Burn rate | Long window | Short window | For | Budget gone in | Severity |
|---|---|---|---|---|---|---|
| `...BurnFast` | 14.4 | 1 h | 5 m | 2 m | about 2 days | critical (page) |
| `...BurnSlow` | 6 | 6 h | 30 m | 15 m | 5 days | warning (ticket) |

The line is `burn rate x budget`: for availability 14.4 x 0.001 = 1.44% of requests, and 6 x 0.001 = 0.6%; for latency 14.4 x 0.01 and 6 x 0.01; for delivery lag 14.4 x 0.005 and 6 x 0.005 of the time. The ratios over each window are recorded by the file's `smo.recording` group (`smo:gateway_error_ratio:rate1h`, ...), so a dashboard can plot the same lines the alerts use. If you change a target, change the budget in the rule and in the table above together (the test checks the pairs, not the numbers).

A burn alert needs traffic. With none, the ratio is empty and the alert cannot fire; a gateway that receives no requests at all is caught by `SmoModuleDown`, not by the SLO.

## Other alerts

Cause-level alerts that are not SLOs: `SmoModuleDown`, `SmoModuleHighErrorRate`, `SmoModuleLatencyHigh`, `SmoOutboxDeadRows`, `SmoOutboxBacklog`, `SmoDbPoolNearlyExhausted`, `SmoOutboundCallsFailing`, `SmoGatewayRateLimiting`, `SmoAuthRefusalsHigh`, `SmoWorkerTaskFailing`, `SmoRAppInstancesFaulted`. Their thresholds are guesses to tune against a week of real traffic; each page in `runbooks/` says what to check.

## Using the rules

- Prometheus: `rule_files: [smo-alerts.rules.yaml]` (the file is a plain rule file). Scrape each module's `/metrics` as a job named after the module.
- Helm with the Prometheus Operator: `--set prometheusRule.enabled=true` (default off; `prometheusRule.labels` is what your Prometheus selects on). The chart README says more.
- `tests_integration/test_alert_rules.py` is the CI check (structure, every metric and label exists in the code, every `runbook_url` resolves, every SLO alert is in this document; `promtool check rules` as well when it is installed).

## What is not here

No alert on a database that is not scraped (Postgres is seen through the pool and the 5xx alerts), none on the expiry of the edge's TLS certificate (the deployment's; the service-to-service certificates have `SmoMtlsCertExpiring`), none on backups (`PR-HA-6`). The worker's task counters need the worker's metrics port (`SMO_WORKER_METRICS_PORT`) scraped, which neither compose nor the chart does yet. Targets stay proposed until an operator has run them against real traffic.
