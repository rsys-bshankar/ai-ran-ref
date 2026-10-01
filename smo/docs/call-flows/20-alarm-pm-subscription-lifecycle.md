# Call Flow: Alarm Raise → Ack → Clear (Correlated) + PM/FM Subscription → DME Registration

RAN NF OAM's fault and performance subscription surface: a correlated multi-alarm raise →
ack → clear sequence (TS 28.111 fault NRM; HISTORY.md SA-RANOAM-5, SA-RANOAM-6), and
`SubscribePM`/FM subscriptions, whose real side effect is registering RAN NF OAM itself as
a DME producer (TS 28.550 PM job control; HISTORY.md SA-RANOAM-7, OI-6.7). Call flow 03
mentions fresh alarm-UUID minting and call flow 01 an ordinary rApp's
`/dme/production-capabilities` registration; this flow walks both topics in full.

```mermaid
sequenceDiagram
    actor Source as RAN NF (raising alarms)
    participant NFOAM as RAN NF OAM
    actor Operator
    participant DME as DME

    rect rgb(240, 255, 240)
    Note over Source,NFOAM: Correlated raise — one root cause, two symptomatic alarms
    Source->>NFOAM: POST /alarms/ingest (sourceAlarmId=A1, ME, severity=critical,<br/>correlationGroup=G1, rootCauseIndicator=true, probableCause, specificProblem)
    NFOAM->>NFOAM: mint a fresh alarmId (UUID) — never trusts the raising ME's own<br/>native sourceAlarmId directly (R1UCR's fleet-wide collision risk)
    NFOAM-->>Source: alarmId=X1
    Source->>NFOAM: POST /alarms/ingest (sourceAlarmId=A2, ME, severity=major,<br/>correlationGroup=G1, rootCauseIndicator=false, correlatedNotifications=[X1])
    NFOAM-->>Source: alarmId=X2
    end

    rect rgb(240, 248, 255)
    Note over Operator,NFOAM: Acknowledge, then clear — two separate lifecycle steps
    Operator->>NFOAM: GET /alarms?managed_element_ref=ME&severity=critical
    NFOAM-->>Operator: [alarm X1]
    Operator->>NFOAM: PATCH /alarms/X1/ack (newState=ACKNOWLEDGED, ackUserId)
    NFOAM->>NFOAM: ackState=ACKNOWLEDGED, ackUserId set, changedAt=now()
    NFOAM-->>Operator: updated alarm

    Note over Source,NFOAM: root cause resolved on the real NF
    Source->>NFOAM: PATCH /alarms/X1/clear (clearUserId)
    NFOAM->>NFOAM: severity="cleared" (reuses the severity CHECK constraint's own<br/>legal value — a separate lifecycle field was deliberately not added),<br/>clearedAt=changedAt=now()
    NFOAM-->>Source: updated alarm

    Operator->>NFOAM: GET /alarms?severity=cleared
    NFOAM-->>Operator: [alarm X1] — X2 (the symptomatic alarm) is untouched —<br/>clearing the root cause doesn't cascade-clear its correlated alarms
    end

    rect rgb(255, 240, 240)
    Note over Operator,DME: PM subscription — a DME-producer registration wrapper, not a clause-8 job
    Operator->>NFOAM: POST /pm-subscriptions (managedElementRef, counterType, deliveryMethod=push, granularityPeriod)
    NFOAM->>NFOAM: southboundEngine = {pull:ProvMnS, push:PMJobControl, stream:StreamingDataReporting}[deliveryMethod]
    NFOAM->>DME: RegisterDMEType(namespace=RAN, name=PMCounters.{counterType},<br/>producerId=ran-nf-oam, producerHealthCallbackUrl, jobCallbackUrl)
    Note over NFOAM,DME: RAN NF OAM registers ITSELF as a DME producer for this<br/>counter type — the real cross-service call this wrapper's<br/>own name doesn't advertise
    DME-->>NFOAM: registrationId
    NFOAM-->>Operator: subscriptionId, southboundEngine, granularityPeriod

    Operator->>NFOAM: GET /pm-subscriptions?managed_element_ref=ME
    NFOAM-->>Operator: [PMSubscription, ...]
    Operator->>NFOAM: DELETE /pm-subscriptions/{subscriptionId}
    Note over Operator,NFOAM: idempotent, matching every other subscription-shaped<br/>resource's own unsubscribe route (see "Key decisions")
    NFOAM-->>Operator: 204
    end

    rect rgb(230, 240, 255)
    Note over Operator,DME: FM subscription (HISTORY.md OI-6.7, closed) — same wrapper<br/>shape as PM, one shared type instead of one per counter
    Operator->>NFOAM: POST /fm-subscriptions (managedElementRef, deliveryMethod=push)
    NFOAM->>NFOAM: southboundEngine = {pull:FaultMnS, push:FaultMnS, stream:StreamingDataReporting}[deliveryMethod]
    NFOAM->>DME: RegisterDMEType(namespace=RAN, name=FaultRecords,<br/>producerId=ran-nf-oam, producerHealthCallbackUrl, jobCallbackUrl)
    Note over NFOAM,DME: every subscribing ME joins the SAME RAN.FaultRecords type —<br/>unlike PM's per-counterType identity, FM has no per-ME split —<br/>this is exactly the many-producers-one-type join call flow 11 walks
    DME-->>NFOAM: registrationId
    NFOAM-->>Operator: subscriptionId, southboundEngine

    Operator->>NFOAM: GET /fm-subscriptions?managed_element_ref=ME
    NFOAM-->>Operator: [FMSubscription, ...]
    Operator->>NFOAM: DELETE /fm-subscriptions/{subscriptionId}
    Note over Operator,NFOAM: idempotent, same shape as pm-subscriptions' own unsubscribe
    NFOAM-->>Operator: 204
    Note over NFOAM: this only ever closes the VISIBILITY gap — DME/a consuming rApp<br/>still never clears an alarm — that stays the Ack/Clear block above,<br/>called by the source NF or an operator, unaffected by FM registration
    end
```

**FM subscriptions** (HISTORY.md OI-6.7). `POST /fm-subscriptions` mirrors `subscribe_pm`:
it registers RAN NF OAM as a DME producer for a single, shared `RAN.FaultRecords` type,
so an rApp or AI/ML model that wants outstanding-alarm or alarm-history context during
inference (or during Training/Validation/Emulation, call flow 02's execution runtimes) can
get it through DME rather than calling `GET /alarms` on RAN NF OAM directly. Every
subscribing ME's alarms join that one type instead of getting a type of their own, since
alarms (unlike PM counters) have no natural per-counter-type split to key on. This covers
*visibility* only: there is no route through which DME or a consuming rApp clears an alarm
— clearing stays RAN NF OAM's `PATCH /alarms/{id}/clear`, called by the source NF or an
operator, unaffected by whether FM is DME-registered.

**Key decisions this flow depends on:**
- `correlationGroup`/`correlatedNotifications`/`rootCauseIndicator` are caller-declared, not computed by RAN NF OAM itself — this build carries the correlation a raising source already knows; it doesn't run its own root-cause-analysis algorithm (OPEN_ITEMS.md OI-1-alarm-storm).
- Clearing an alarm never cascades to its correlated siblings — `clear_alarm` only ever mutates the one `alarmId` it's called against; a correlation group with a cleared root cause and un-cleared symptomatic alarms is a real, representable state, not a bug.
- `SubscribePM` is explicitly *not* a clause-8 PM job — RAN NF OAM LLD section 3.5's documented design intent, confirmed by this route's shape: it's a DME-producer registration wrapper (`RegisterDMEType` under the hood) that also records `granularityPeriod`, the one job-control field judged worth keeping despite the wrapper scope cut. `schedule`/`priority`/`multi-instance`/`reportingPeriod` all stay out.
- `DELETE /pm-subscriptions/{id}` is idempotent, matching every other subscription-shaped resource's unsubscribe route in this build (DME's type subscriptions, MDAF's, A1 Related's EI jobs, Intent Service's RMIH registration, MLMF's); the GUI's PM subscriptions table has a matching "Unsubscribe" action (HISTORY.md OI-3-pm-unsubscribe).
- PM and FM subscriptions are gated by the axis-2 presence guard: when the ME's vendor has a registered capability, it must implement PM or FM respectively (`O1_SERVICE_NOT_SUPPORTED` otherwise; call flow 21).
