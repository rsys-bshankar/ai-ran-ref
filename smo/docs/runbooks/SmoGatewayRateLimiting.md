# SmoGatewayRateLimiting

Severity: **info**. Fires when: The gateway refused more than one request per second with `429 RATE_LIMITED` for 10 minutes.
Rule: `deploy/helm/smo/files/smo-alerts.rules.yaml`. Index and template: [README.md](README.md).

## Symptom

`smo_refusals_total{module="r1-termination",reason="rate_limited"}` grows steadily. One caller (an invoker id) is above its token bucket (`R1_RATE_PER_SECOND` 100, `R1_RATE_BURST` 200).

## Impact

The throttled caller gets `429` with `Retry-After`; other callers are unaffected (one bucket per invoker). If the caller is a well-behaved rApp with a legitimate peak, its work is delayed.

## Diagnosis

Commands are for the compose lab; in the chart use `kubectl -n <namespace> logs deploy/<module>` and `kubectl exec` the same way. `<module>` is the `job` label of the alert.

```bash
docker compose logs --since 15m r1-termination | grep -E "\"status\": ?429" | tail -20      
docker compose exec r1-termination python -c "import urllib.request as u; print(u.urlopen('http://localhost:8000/metrics').read().decode())" | grep 'smo_refusals_total'
```
The access log line carries the caller; count the repeats per caller from it. A tight loop without backoff in an rApp is the common cause (`smo_sdk` honours `Retry-After`).

## Mitigation

- Fix the caller: backoff and honour `Retry-After`; batch calls.
- A legitimate higher rate: raise `R1_RATE_PER_SECOND` / `R1_RATE_BURST` for the gateway and note that each replica counts for itself (`docs/ARCHITECTURE.md`).
- A hostile caller: revoke its credential (terminate the rApp instance, or disable the invoker in SME).

## Escalation

The caller's owner. Security contact if the invoker is not a known rApp.
