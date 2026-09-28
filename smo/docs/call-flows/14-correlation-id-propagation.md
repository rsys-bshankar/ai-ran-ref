# Call Flow: Correlation-ID Propagation Across a Multi-Service Fan-Out

Stitches together Wave 3's closing cross-cutting standardization item
(`shared/smo_shared/correlation.py`, `AI_PLATFORM_BASELINE.md`): every other slice
(OAuth2/JWT+Versioning, Error Schema, Pagination, Subscriptions) shipped without this, so
one inbound request's fan-out across several services had no way to be tied back together
in logs or traces. This flow shows the mechanics directly, rather than a business scenario
— reusing call flow 10's own three-step `ServiceOrder` (`INFRA`→`TRAINING`→`DEPLOY`) as
the fan-out that makes propagation observable.

```mermaid
sequenceDiagram
    actor Operator
    participant R1 as R1 Termination
    participant SO as SO SMOS
    participant Focom as FOCOM
    participant AIMgF as AIMgF

    Operator->>R1: POST /so-smos/orders (no X-Correlation-ID header)
    R1->>R1: apply_correlation_id middleware:<br/>no header present -> mint a fresh UUID, C1
    R1->>SO: (proxied) SubmitServiceOrder — X-Correlation-ID: C1<br/>(r1-termination's own generic proxy always sets this to the<br/>current request's real ID, overriding anything it received)

    Note over SO: SO SMOS's own apply_correlation_id middleware sees an<br/>inbound X-Correlation-ID (C1) and reuses it — never mints a second one

    SO->>Focom: dispatch_infra — X-Correlation-ID: C1<br/>(R1Client reads get_correlation_id() and attaches it automatically —<br/>SO SMOS's own dispatch code never handles this header itself)
    Focom-->>SO: 200 {resourceId, clusterId}

    SO->>AIMgF: dispatch_training — X-Correlation-ID: C1 (same ID, same request)
    AIMgF-->>SO: 201 {trainingJobId}

    SO-->>R1: orderId, steps=[...]
    R1-->>Operator: 200, X-Correlation-ID: C1 (echoed back)

    Note over Operator,AIMgF: One log line in SO SMOS, one in FOCOM, one in AIMgF — all<br/>three now carry C1, even though the operator never supplied one

    rect rgb(240, 248, 255)
    Note over Operator,R1: A caller that already has its own tracing ID keeps it
    Operator->>R1: POST /so-smos/orders (X-Correlation-ID: caller-trace-99)
    R1->>R1: apply_correlation_id: header present -> reuse it verbatim, no new UUID minted
    R1->>SO: (proxied) SubmitServiceOrder — X-Correlation-ID: caller-trace-99
    Note over SO,AIMgF: propagates unchanged through the same fan-out —<br/>the operator's own external trace ID survives the whole request
    end
```

**Key decisions this flow depends on:**
- The ID lives in a `ContextVar`, not a request parameter every handler has to thread through — `get_correlation_id()` reads whatever the current request's middleware set, and `R1Client` (every service's own cross-service caller) attaches it automatically to every outbound call. No dispatcher (`dispatch_infra`/`dispatch_training`/`dispatch_deploy`, call flow 10) or route handler had to change to participate.
- **r1-termination is the one place that overrides rather than reuses** — its own generic proxy always forwards `get_correlation_id()`'s value (the ID *this* request was assigned or reused), even if the original inbound call happened to carry a different, stale, or malformed header of its own. Every other service's own `apply_correlation_id` middleware reuses an inbound header verbatim if present.
- Deliberately not in any OpenAPI schema — a header injected by middleware on every route isn't a per-operation contract element; declaring it as a formal parameter on ~200 operations across 18 services would be a much larger, largely cosmetic diff for no real behavior gain (confirmed by the OpenAPI-drift integration test, which stayed green with zero spec regeneration needed when this shipped).
- No formal 3GPP/O-RAN spec defines a header for this exact purpose — `TS29500_CustomHeaders.abnf`'s own `3gpp-Sbi-Correlation-Info` is a different concept (subscriber-identity correlation: imsi/msisdn/impu, not request tracing), confirmed by reading its ABNF grammar directly rather than assumed from the name. `X-Correlation-ID` is this build's own HTTP-native name for the same idea `smo-teiv`'s own CloudEvent `correlationid` tracks over a different transport.
