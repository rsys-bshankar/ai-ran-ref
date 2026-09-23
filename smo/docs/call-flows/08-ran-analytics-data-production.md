# Call Flow: RAN Analytics — Producer Registration → Report → Subscriber Query

Stitches together RAN Analytics LLD section 1: `RegisterAnalyticsProducer` closes the
producer-side gap v1.3 left entirely unmodeled (v1.3 had Subscribe/Unsubscribe/Query, but
no way for a producer to register at all), and section 2's distinction from AI/ML
Workflow's MLMF — this is RAN *behavior* analytics (MDAF), not model performance.

```mermaid
sequenceDiagram
    actor Producer as Analytics Producer rApp
    participant R1 as R1 Termination
    participant RanA as RAN Analytics SMOS
    participant SME as SME
    actor Consumer as Analytics Consumer rApp (e.g. SA SMOS)

    Producer->>R1: POST /ran-analytics/producers (producerId, analyticsType, dmeInputTypes, outputSchema)
    R1->>RanA: (proxied) RegisterAnalyticsProducer
    RanA->>RanA: upsert MDAFProducer on (producer_id, analytics_type) —<br/>re-registering the same pair updates in place, not a conflict
    RanA->>SME: RegisterService (serviceName=mdaf.{analyticsType}, serviceCapabilities.analyticsType)
    SME-->>RanA: serviceId
    RanA-->>Producer: {status: registered}

    Consumer->>R1: POST /ran-analytics/subscriptions (analyticsType, requestedBy, notificationDestination?)
    R1->>RanA: (proxied) SubscribeAnalytics
    RanA-->>Consumer: subscriptionId

    loop per analytics cycle
        Producer->>R1: POST /ran-analytics/reports (analyticsType, output, inputSources, scope?)
        R1->>RanA: (proxied) PublishAnalyticsReport
        RanA->>RanA: persist MDAFReport
        RanA->>Consumer: best-effort POST notificationDestination (reportId, analyticsType, output, inputSources)
        Note over RanA,Consumer: A subscription with no notificationDestination stays pull-only —<br/>delivery is never guessed from requestedBy.
        RanA-->>Producer: {reportId}
    end

    Consumer->>R1: GET /ran-analytics/reports?analytics_type=...
    R1->>RanA: (proxied) QueryAnalyticsReport
    RanA-->>Consumer: [MDAFReport, ...] — pull-based retrieval, the only working delivery path

    Consumer->>R1: DELETE /ran-analytics/subscriptions/{id}
    R1->>RanA: (proxied) UnsubscribeAnalytics
```

**Key decisions this flow depends on:**
- `MDAFProducer`'s primary key is `(producer_id, analytics_type)` — the same producer registering a second `analyticsType` is a distinct row, not an update; re-registering the *same* pair (e.g. on restart) upserts in place rather than crashing on the composite-key conflict (fixed this pass — see `smo/README.md`'s "Real bugs this pass found").
- RAN Analytics registers its producer's capability through SME (`RegisterService`), making it independently discoverable via `service-apis` like any other R1 service — not a private RAN-Analytics-only registry.
- **Closed since this flow was first written**: `PublishAnalyticsReport`'s subscriber-notification loop used to be a deliberate no-op (`for sub in subs: pass`) — a `MDASubscription`'s `requestedBy` was recorded but never actually called back. `SubscribeAnalytics` now also accepts an optional `notificationDestination` (same shape as A1 Related's/Policy Mgmt's own subscription callbacks), and a published report is best-effort POSTed to every matching subscriber that registered one, same delivery guarantees as those two (an unreachable subscriber never fails the publish). A subscriber that never registers a destination stays pull-only via `QueryAnalyticsReport`.
- This is architecturally distinct from AI/ML Workflow's MLMF (call flow 02) even though both look like "metrics in, subscribers out" — RAN Analytics' `analyticsType` describes RAN *behavior* (coverage, interference, resource utilization), never a model's own performance, which stays MLMF's domain exclusively (RAN Analytics LLD section 2).
