# Call Flow: Alarm Raise → Ack → Clear (Correlated) + PM Subscription → DME Registration

Stitches together `OPEN_ITEMS.md` section 5's fault-lifecycle closure and `SPEC_AUDIT.md`'s
TS28111_FaultNrm/TS28550_PerfMeasJobCtrlMnS findings — both only ever touched in passing
notes elsewhere (call flow 03's own aside on fresh alarm-UUID minting; call flow 01's own
`/dme/production-capabilities` registration for an ordinary rApp producer). This is the
dedicated walkthrough of each on its own terms: a correlated multi-alarm raise/ack/clear
sequence, and `SubscribePM`'s own real side effect of registering RAN NF OAM itself as a
DME producer.

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
    NFOAM-->>Operator: [alarm X1] — X2 (the symptomatic alarm) is untouched;<br/>clearing the root cause doesn't cascade-clear its correlated alarms
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
    Note over Operator,NFOAM: no DELETE /pm-subscriptions/{id} route exists in this build —<br/>unlike DME/MDAF/A1-Related/Intent Service/MLMF, a PM subscription<br/>can be created and listed but never torn down (see "Key decisions")
    end
```

**Key decisions this flow depends on:**
- `correlationGroup`/`correlatedNotifications`/`rootCauseIndicator` are caller-declared, not computed by RAN NF OAM itself — this build carries the correlation a raising source already knows, it doesn't run its own root-cause-analysis algorithm (matching `OPEN_ITEMS.md`'s own confirmed elision: "Alarm-storm correlation algorithm... nothing implemented").
- Clearing an alarm never cascades to its correlated siblings — `clear_alarm` only ever mutates the one `alarmId` it's called against; a correlation group with a cleared root cause and un-cleared symptomatic alarms is a real, representable state, not a bug.
- `SubscribePM` is explicitly *not* a clause-8 PM job — RAN NF OAM LLD section 3.5's own documented design intent, confirmed by this route's very shape: it's a DME-producer registration wrapper (`RegisterDMEType` under the hood) that happens to also record `granularityPeriod`, the one job-control field judged worth keeping despite the wrapper scope cut. `schedule`/`priority`/`multi-instance`/`reportingPeriod` all stay out.
- **Gap surfaced by writing this flow, not previously documented**: every other subscription-shaped resource in this build (DME's type subscriptions, MDAF's, A1 Related's EI jobs, Intent Service's RMIH registration, MLMF's) has a real `DELETE`/unsubscribe route. `PMSubscription` doesn't — `POST /pm-subscriptions` and `GET /pm-subscriptions` both exist, but there is no `DELETE /pm-subscriptions/{id}` anywhere in `ran-nf-oam/app/main.py`. Worth adding to `OPEN_ITEMS.md`: a subscription created here can never be torn down through this build's own API, only by direct DB access.
